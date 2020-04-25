---
title: "This Goes to Eleven (Pt. 6/∞)"
excerpt: >
  Decimating Array.Sort with AVX2.<br/><br/>
  I ended up going down the rabbit hole re-implementing array sorting with AVX2 intrinsics.<br/>
  There's no reason I should go down alone.
header:
  overlay_image: url('/assets/images/these-go-to-eleven.jpg'), url('/assets/images/these-go-to-eleven.webp')
  overlay_filter: rgba(106, 0, 0, 0.6)
  og_image: /assets/images/these-go-to-eleven.jpg
  actions:
    - label: "GitHub"
      url: "https://github.com/damageboy/vxsort"
    - label: "Nuget"
      url: "https://www.nuget.org/packages/VxSort"
hidden: true
published: false
date: 2020-02-03 05:22:28 +0300
classes: wide
#categories: coreclr intrinsics vectorization quicksort sorting
---

I ended up going down the rabbit hole re-implementing array sorting with AVX2 intrinsics, and there's no reason I should go down alone.

Since there’s a lot to go over here, I’ll split it up into a few parts:

1. In [part 1]({% post_url 2020-01-28-this-goes-to-eleven-pt1 %}), we did a short refresher on `QuickSort` and how it compares to `Array.Sort`. If you don’t need any refresher, you can skip over it and get right down to part 2 and onwards , although I really recommend skimming through, mostly because I’ve got really good visualizations for that should be in the back of everyone’s mind as we’ll be dealing with vectorization & optimization later.
2. In [part 2]({% post_url 2020-01-29-this-goes-to-eleven-pt2 %}), we go over the basics of Vectorized HW Intrinsics, discussed vector types, and a handful of vectorized instructions we’ll actually be using in part 3, but we still will not be sorting anything.
3. In [part 3]({% post_url 2020-01-30-this-goes-to-eleven-pt3 %}) we go through the initial code for the vectorized sorting and we’ll finally start seeing some payoff. We’ll finish with some agony courtesy of CPU’s Branch Predictor, just so we don't get too cocky.
4. In this part, we go over a handful of optimization approaches that I attempted trying to get the vectorized partition to run faster, we'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of 100% of the remaining scalar code, by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization and gain a considerable amount of performance / efficiency in the process.
6. Finally, in part 6, I’ll list the outstanding stuff / ideas I have for getting more juice and functionality out of my vectorized code.

## (Trying) to squeeze some more vectorized juice

I thought it would be nice to show a bunch of things I ended up trying to improve performance.
I tried to keep most of these experiments in separate implementations, both the ones that yielded positive results and the failures. These can be seen in the original repo under the [Happy](https://github.com/damageboy/VxSort/tree/research/VxSortResearch/Unstable/AVX2/Happy) and [Sad](https://github.com/damageboy/VxSort/tree/research/VxSortResearch/Unstable/AVX2/Sad) folders.

While some worked, and some didn't, I think a bunch of these were worth mentioning, so here goes:

### Mitigating the bad speculation: :-1:, then:+1:

I postponed answering the last question I raised in the end of part 3 for last. If you recall, we experienced a lot of bad-speculation effects when sorting the data with our vectorized code, and profiling using hardware counters showed us that while `InsertionSort` was the cause of most of the bad-speculation events (41%), our vectorized code was still responsible for 32% of them. I've been writing again and again that our vectorized code is branch-less, why would branch-less code be causing bad speculation? Shouldn't we be doing no speculation at all?

Oh, I wish it were true, remember this little gem, that we have to use in every iteration in order for us to successfully do in-place partitioning?

```csharp
int * nextPtr;
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight)) {
    nextPtr = readLeft;
    readLeft += 8;
} else {
    nextPtr = readRight;
    readRight -= 8;
}
```

This is it! We ended up sneaking up a data based branch into our code in the form of this side-selection logic. Whenever we try to pick a side we would read from next, this is where we put the CPU in a tough spot. We're asking it to speculate on something it *can't possibly speculate on successfully*. Our question is: "Oh CPU, CPU in the socket, Which side is closer to being over-written of them all?", to which the answer is completely data-driven! In other words, it depends on how the last round(s) of partitioning mutated all 4 pointers involved in the comparison. While it might sound like an easy thing for the CPU to check, we have to remember it is actually required to *speculate* this ahead of time, since every time the CPU is demanded to answer this question, it it is **still in the middle** of processing a few of the previous iterations of this very same hot-loop due to the length of the pipeline and the nature of speculative execution. So the CPU guesses, at best, on stale data, and we know, as the grand designers of this mess that in reality, at least for random data, the best guess is no better here than flipping a coin. Quite sad. You have to admit it is quite ironic how we managed to do this whole big circle around our own tails just to come-back to having a branch mis-prediction based on the random array data.

Mis-predicting here is unavoidable. Or at least I have no idea on how to avoid it in C# with the current JIT in August 2019 (But oh, just you wait for part 6, I have something in store there for you..., hint hint, wink wink).

But not all is lost.

#### Replacing the branch with arithmetic: :-1:

Could we replace this branch with arithmetic just like I showed a couple of paragraphs above?  Well, We could, except that it runs more slowly:

Consider this alternative version:

```chsarp

```

This code has a few effects:

* It make me want to puke
* It eliminates branch mis-prediction in our vectorized partitioning path almost entirely:
  * I measured < 5% mis-prediction with this
* It generates SO much additional code that its simply just not worth it!

So while this attempt seems futile for now, we see that it fails for the "wrong" reason. We **did** manage to eliminate the mis-prediction, it simply looks like the price is too high. This is again a mid-way conclusion I will get back to in a future post.

#### Unrolling the code: :+1:

We can still do something about this! We can unroll the code!  
Unrolling loop code is a common technique that is usually employed to reduce the loop overhead (maintaining the loop counters and checking the loop end-condition) by doing more actual work per-loop (e.g. calling the same code multiple times per iteration). It's classically thought of as a complexity/overhead trade-off: We write somewhat more complex code, but we end up with less overhead for the "fluff" code of maintaining a loop counters and checking the end condition. I've [previously described]({% post_url 2018-08-19-netcoreapp3.0-intrinsics-in-real-life-pt2%}) how/why this helps, but the concept is clear: We do more work per iteration, there-by paying less overhead per work done.

<span uk-icon="icon: info; ratio: 2"></span>  
It should probably be noted that normally, unrolling this hot-loop wouldn't do much good, since the amount of instructions attributed to overhead (loop-control) vs. the actual work done here, is not so skewed that we should immediately run to unroll. However, I propose a different way of thinking about this: We shouldn't measure amount of instructions of work/overhead but measure cycles attributed for work/overhead. With that in mind, it is clear that even a single branch instruction, when frequently mis-speculated, is very expensive, cycle wise.
{: .notice--info}

The same unrolling technique can be used to mitigate our bad speculation, to some extent. While we can't fix the rate of mis-speculation, we can change the mix between time we're penalized for each mis-speculation and actual work being done: we can, for example, use a loop-body that does 4 vectorized blocks *per iteration*. We would still speculate poorly in terms of success rate, as we did before, but we would have 4x less branches mis-speculated in absolute terms, since we would re-write the code to calculate which side to read from once per every 4 blocks.

I won't show the entire code listing for this, as it really ended up blowing up, and complicating the code. You can look into it here. What I will show is where we are after unrolling by 4 vectorized blocks, we are finally able to alleviate our mis-prediction pains for the first time:

### Out of juice?

Well, I'm personally out of ideas about to optimize the vectorized code for now.

I kept saying this to myself when this blog post was half the size, but this journey with optimizing this particular part of the code, the partitioning functions, appears to have come to an end.

Let's show where we are, when compared to the original `Array.Sort` I set out to beat in the beginning of the first post, when we were all still young and had a long future ahead of us :)

...

We are now running at almost 4x the original array

It is also interesting to take a look at the various profiles we showed beofre:





Not bad, all in all. We are now partitioning using vectorized code pretty quickly, and this is a good time to finally end this post.  
In the next post we will move on to replacing `InsertionSort`. Right now, this is the last big chunk of scalar code we are still running, and with all the previous optimization efforts it is now taking up around half of the time we're actually spending on sorting. Can we make it? Stay tuned!

### Get rid of local initialization for all methods: :+1:

While this isn't "coding" per-se, I think it's worthwhile mentioning in this series: Historically, the C# compiler emits the `localsinit` flag on all methods that declare local variables. This flag, which can be seen in .NET MSIL disassembly, instructs the JIT to generate machine code that zeros out the local variables as the function starts executing. While this isn't a bad idea in itself, it is important to point out that this is done even though the C# compiler is already rather strict and employs definite-assignment analysis to avoid having uninitialized locals at the source-code level to begin with... Sounds confusing? Redundant? I thought so too!  
To be clear: Even though we are *not allowed* to use uninitialized variables in C#, and the compiler *will* throw those [`CS0165` errors](https://docs.microsoft.com/en-us/dotnet/csharp/language-reference/compiler-messages/cs0165) at us and insist that we initialize everything like good boys and girls, the emitted MSIL will still instruct the JIT to generate **extra** code, essentially double-initializing locals: first with `0`s thanks to `localinit` and then as we initialize them from C#. Naturally, this adds more code to decode and execute, which is not OK in my book. This is made worse by the fact that we are discussing this extra code in the context of a recursive algorithm where the partitioning function is called hundreds of thousands of times for sizeable inputs (you can go back to the 1<sup>st</sup> post to remind yourself just how many times the partitioning function gets called per each input size, hint: it's a lot!).

There is a [C# language proposal](https://github.com/dotnet/csharplang/blob/master/proposals/skip-localsinit.md) and a [PR that was already merged](https://github.com/dotnet/runtime/pull/454) as part of .NET 5.0 that allows developers to get around this weirdness, but in the meantime, with .NET Core 3.1, I ended up devoting 5 minutes of my life to use the excellent [`LocalsInit.Fody`](https://github.com/ltrzesniewski/LocalsInit.Fody) weaver for [Fody](https://github.com/ltrzesniewski/LocalsInit.Fody) which can re-write assemblies to get rid of this annoyance. I encourage you to support Fody through open-collective as it is a wonderful project that serves so many backbone projects in the .NET World.

At any rate, we have lots of locals, and we are after all implementing a recursive algorithm, so this has a substantial effect on performance:



Not bad: a 1%-3% improvement (especially for larger array sizes) across the board for practically doing nothing...

### Paying old debts: Squeezing a few more bytes

There is yet one last thing we can do here: I'm still wasting 3 bytes in the instruction stream and an entire cycle to negate the `PopCount` result. Can this be avoided? Sure can! But this will come at a cost (for me):

The way we can get rid of this negation is by essentially re-writing partitioning block:

* We will start by reversing the order of operands passed to the `CompareGreaterThan` intrinsic.
* Once that order is reversed, the result of the operation and the `mask` variable where we store the resulting bits is also reversed in its meaning:  
  we end up having `1` bits in the `mask` variable marking elements that are now *smaller-than* the pivot.
  * This changes the partitioning dynamics a bit, but everything remains "legal" in that respect.
* Once the `1` bits mark elements *smaller-than* the pivot, we also need a new permutation table! Or to be more precise, we need the same type of permutation entries in every respect except that their ordering needs to adhere to the new comparison method.
* Finally, the same `PopCount` operation will now count elements that end up on the *left* side of the array.  
  All this was done so that we could now update the pointers directly with the `popCount` variable, without negating it on the one hand, and with addition operations on the other hand!

Here is code for the final pointer update code in C#:

```csharp
    // This popCount counts elements that go to the left!
    var leftPopCount = PopCnt.PopCount(mask); 
    writeLeft  = writeLeft + leftPopCount;
    writeRight = writeRight + leftPopCount - 8;
```

This C# code essentially achieves the same pointer updates we had before except it does so without the negation. We do have to subtract `8` while updating the `writeRight` pointer, but that was never an issue for the JIT when considering to use `LEA`: The JIT simply encodes the twos-complement value into the instruction stream, and lets the normal addition semantics for LEA pick it up from there. The final assembly code looks like this:

```nasm
popcnt  rdi,rdi             ; F3480FB8FF
lea     rax,[rax+rdi*4]     ; 488D04B8
lea     r15,[r15+rdi*4-20h] ; 4D8D7CBFE0
```

This further helps by compressing the pointer mutation code down from `12` to `9` bytes! All in this is quite a lot of savings compared to the `20` we started with. These savings will pay in spades in the next posts, as we continue to work on speeding up this vectorized partitioning.



----



In the last post, I will address a way to avoid branches at this point at all. It's an optimization technique that converts the branch into a data dependency, which is the way to go for badly speculative code.  
This optimization relies on a widely available Intel opcode called `CMOV` (conditional mov). While C++/go/rust compilers support using this (mostly due to their use of LLVM as an optimizing compiler back-end), at the time of writing this, August 2019, we, as C# developers, don't have a JIT that supports this feature while also supporting AVX2 intrinsics (Mono has some limited `CMOV` support, but no intrinsics support), and there are no intrinsics for this in C# available. I will show how/what could be done with this in the last blog post in this series, but for now, having the JIT magically solve our mis-prediction woes for us is off the table.

So what's so special about SIMD registers?  

Again, not *much*. According to the specfic CPU we're running our code on, we'll get access to a different set of vectorized registers, varying in their size / width:

<table style="text-align: center; line-height: normal;">
<tbody><tr>
<td style="width: 600; border: none; border-right: 1px solid black; font-size: xx-small;"><span style="float: left">511</span> <span style="float: right">256</span></td>
<td style="width: 25%; border: none; border-right: 1px solid black; font-size: xx-small;"><span style="float: left">255</span> <span style="float: right">128</span></td>
<td style="width: 25%; border: none; border-right: 1px solid black; font-size: xx-small;"><span style="float: left">127</span> <span style="float: right">0</span></td>
</tr><tr>
<td style="border-top: none; border-right: 1px solid black;"></td>
<td style="border-top: none; border-right: 1px solid black;"></td>
<td style="border-top: none; border-right: 1px solid black;"></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm0        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm0        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm0        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm1        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm1        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm1        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm2        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm2        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm2        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm3        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm3        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm3        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm4        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm4        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm4        </pre></td>
</tr>
<tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm5        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm5        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm5        </pre></td>
</tr>
<tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm6        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm6        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm6        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm7        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm7        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm7        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm8        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm8        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm8        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm9        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm9        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm9        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm10        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm10        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm10        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm11        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm11        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm11        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm12        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm12        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm12        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm13        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm13        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm13        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm14        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm14        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm14        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm15        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ddd"><pre>ymm15        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black; background: #ccc"><pre>xmm15        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm16        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm16        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm16        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm17        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm17        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm17        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm18        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm18        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm18        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm19        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm19        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm19        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm20        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm20        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm20        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm21        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm21        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm21        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm22        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm22        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm22        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm23        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm23        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm23        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm24        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm24        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm24        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm25        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm25        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm25        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm26        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm26        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm26        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm27        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm27        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm27        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm28        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm28        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm28        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm29        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm29        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm29        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm30        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm30        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm30        </pre></td>
</tr><tr>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>zmm31        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>ymm31        </pre></td>
<td style="padding:0; font-size: 0.6em; border-right: 1px solid black"><pre>xmm31        </pre></td>
</tr></tbody></table>

In this table, which I've conveniently taken and adapted from Wikipedia, you can see the various registers into the Intel world of CPUs.

The somewhat small part shaded in gray are the actualy registers available to use through CoreCLR 3.0: those are 16 registers that are either 128 / 256 bits wide (depending if our CPU has SSE / AVX support)

While the rest of the table depicts what is / could be available to us had we were C++ / Rust developers on the best that Intel has to offer.  
I know it immediately feels like we, as C# devs, have been shortchanged, from the table, because all those nice plump 512 bit registers are only for us to see and not use, but in reality, AVX-512 has still not caught on for mere mortals: Every single desktop/mobile CPU doesn't support them at all, and even with servers / workstations, you need to shell out serious change to get access to these registers and (more importantly!) the instructions that come with them.

To sum this up, as C# developers, we get access to 16 architectural 256-bit wide registers. Those can be later mapped on to many more physical registers my the CPUs own registers renaming (which I've written about in the part), and for the most part, 



---

[^0]: Most modern Intel CPUs can actually address the L1 cache units twice per cycle, that means they can actually ask it to read two cache-line as the same time. But this still causes more load on the cache and bus, and we must not forget that we will be reading an additional cache-line for our permutation block...
[^1]: This specific AVX2 intrinsic will actually fail if/when used on non-aligned addresses. But it is important to note that it seems it won’t actually run faster than the previous load intrinsic we’ve used: `AVX2.LoadDquVector256` as long as the actual addresses we pass to both instructions are 32-byte aligned. In other words, it’s very useful for debugging alignment issues, but not that critical to actually call that intrinsic! 
