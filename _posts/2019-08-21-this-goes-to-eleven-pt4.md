---
title: "This Goes to Eleven (Pt. 4/6)"
excerpt: >
  Decimating Array.Sort with AVX2.<br/><br/>
  I ended up going down the rabbit hole re-implementing array sorting with AVX2 intrinsics.<br/>
  There's no reason I should go down alone.
header:
  overlay_image: url('/assets/images/these-go-to-eleven.jpg'), url('/assets/images/these-go-to-eleven.webp')
  overlay_filter: rgba(106, 0, 0, 0.6)
  actions:
    - label: "GitHub"
      url: "https://github.com/damageboy/vxsort"
    - label: "Nuget"
      url: "https://www.nuget.org/packages/VxSort"
hidden: true
date: 2019-08-20 11:26:28 +0300
classes: wide
#categories: coreclr intrinsics vectorization quicksort sorting
---

I ended up going down the rabbit hole re-implementing array sorting with AVX2 intrinsics, and there's no reason I should go down alone.

Since there’s a lot to go over here, I’ll split it up into a few parts:

1. In [part 1]({% post_url 2019-08-18-this-goes-to-eleven-pt1 %}), we did a short refresher on `QuickSort` and how it compares to `Array.Sort`. If you don’t need any refresher, you can skip over it and get right down to part 2 and onwards , although I really recommend skimming through, mostly because I’ve got really good visualizations for that should be in the back of everyone’s mind as we’ll be dealing with vectorization & optimization later.
2. In [part 2]({% post_url 2019-08-19-this-goes-to-eleven-pt2 %}), we go over the basics of Vectorized HW Intrinsics, discussed vector types, and a handful of vectorized instructions we’ll actually be using in part 3, but we still will not be sorting anything.
3. In [part 3]({% post_url 2019-08-20-this-goes-to-eleven-pt3 %}) we go through the initial code for the vectorized sorting and we’ll finally start seeing some payoff. We’ll finish with some agony courtesy of CPU’s Branch Predictor, just so we don't get too cocky.
4. In this part, we go over a handful of optimization approaches that I attempted trying to get the vectorized partition to run faster, we'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of 100% of the remaining scalar code, by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization and gain a considerable amount of performance / efficiency in the process.
6. Finally, in part 6, I’ll list the outstanding stuff / ideas I have for getting more juice and functionality out of my vectorized code.

## (Trying) to squeeze some more vectorized juice

I thought it would be nice to show a bunch of things I ended up trying to improve performance.  
I tried to keep most of these experiments in separate implementations, both the ones that yielded positive results and the failures. These can be seen in the original repo under the [Happy](https://github.com/damageboy/QuicksortAvaganza/tree/master/VxSort/AVX2/Happy) and [Sad](https://github.com/damageboy/QuicksortAvaganza/tree/master/QuickSortAvaganza/AVX2/Sad) folders.

While some worked, and some didn't, I think a bunch of these were worth mentioning, so here goes:

### Dealing with small JIT hiccups: :+1:

One of the more annoying things I've discovered during this optimization process was the the JIT could generate much better code around with pointer arithmetic.  
Consider this following piece of code, which we've shown before:

```csharp
if (readLeft   - writeLeft <= 
    writeRight - readRight) {
    // ...
} else {
    // ...
}
```

It looks innocent enough, but here's the freely commented x86 asm code for it:

```nasm
mov rcx, rdi   ; copy readLeft
sub rcx, r12   ; subtract writeLeft
mov r8, rcx    ; wat?
sar r8, 0x3f   ; wat?1?
and r8, 0x3    ; wat?!?!?
add rcx, r8    ; wat!?!@#
sar rcx, 0x2   ; wat#$@#$@
mov r9, rsi    ; copy writeRight 
sub r9, r13    ; subtract readRight
mov r10, r9    ; wat?
sar r10, 0x3f  ; wat?1?
and r10, 0x3   ; wat?!?!?
add r9, r10    ; wat!?!@#
sar r9, 0x2    ; wat#$@#$@
cmp rcx, r9    ; finally, comapre!
```

As you can see from my comments, quite a few of those instructions were not making me happy...  
The original code made the JIT (wrongfully) think we are want it to perform `int *` arithmetic for `readLeft - writeLeft` and `writeRight - readRight`. In other words: The JIT generated code to take the numerical, or `byte *` pointer differences, and generated extra code to convert them to `int *` differences: so lots of extra arithmetic operations. This is quite pointless: we just care if one side is larger than the other, we don't care if this is done with `byte *` or `int *` units... This is similar to converting two distance measurements taken in `cm` to `km` just to compare which is greater. Redundant.

With my new disillusionment with the JIT I have to write this:

```csharp
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight)) {
    // ...
} else {
    // ...
}
```

By doing this sort of seemingly useless casting, we get the following asm generated:

```nasm
mov rcx, rdi  ; copy readRight
sub rcx, r12  ; subtract writeLeft
mov r9, rdi   ; copy writeRight
sub r9, r13   ; subtract readRight
cmp rcx, r9   ; compare
```

It doesn't take a degree in reverse-engineering asm code to figure out this was a good idea!  
By forcefully casting each pointer to `byte *` we are "telling" the JIT that the comparison can be made without the extra fan-fare.

The same pattern (albeit slightly more convoluted) re-surfaces here:

```csharp
var popCount = PopCount(mask);
writeLeft += 8U - popCount;
writeRight -= popCount;
```

Here, the `popCount` result is used to increment two `int *` values. Unfortunately, the JIT isn't smart enough to see that it would be wiser to left shift `popCount` once by `2` (e.g. convert to `byte *` distance)  and reuse that value twice. So I re-wrote the previous rather clean code into the following god-awful mess:

```csharp
var popCount = PopCount(mask) << 2;
writeRight = ((int *) ((byte *) writeRight - popCount);
writeLeft =  ((int *) ((byte *) writeLeft + 8*4U - popCount);
```

Now we're generating slightly denser code by eliminating silly instructions from a hot loop. But does it help?

| Method                   | N        |  Mean (µs) | Time / N (ns) | Ratio |
| ------------------------ | -------- | -------------: | ------------: | ----: |
| Naive    | 100      |       3.043 |    30.4290 |  1.00 |
| MicroOpt | 100      |       3.076 |    30.7578 |  1.15 |
| Naive | 1000     |      26.041 |    26.0415 |  1.00 |
| MicroOpt | 1000     |      23.257 |    23.2569 |  0.91 |
| Naive | 10000    |     325.880 |    32.5880 |  1.00 |
| MicroOpt | 10000    |     312.971 |    31.2971 |  0.96 |
| Naive | 100000   |   3,510.946 |    35.1095 |  1.00 |
| MicroOpt | 100000   |   3,327.012 |    33.2701 |  0.95 |
| Naive | 1000000  |  27,700.134 |    27.7001 |  1.00 |
| MicroOpt | 1000000  |  26,130.626 |    26.1306 |  0.94 |
| Naive | 10000000 | 298,068.352 |    29.8068 |  1.00 |
| MicroOpt | 10000000 | 275,455.395 |    27.5455 |  0.92 |

Sure does! The improvement is very measurable. Too bad we had to uglify the code to get here, but such is life. Our results just improved by another ~4-9% across the board.  
If this is the going rate for ugly, I'll bite the bullet :)

### Get rid of localsinit flag on all methods: :+1:

While this isn't "coding" per-se, I think it's something that is worthwhile mentioning in this series: Historically, the C# compiler emits the `localsinit` flag on all methods that declare local variables. This is done even though the compiler is rather strict and employs definite-assignment analysis to avoid having uninitialized locals to begin with... Sounds confusing? Redundant? I thought so too!  
To be clear... Even though it is *not allowed* to use uninitialized variables in C#, and the the compiler *will* insist that we initialize everything like good boys and girls, the emitted MSIL still tells the JIT to generate **extra** code that initializes these locals before we get to initialize them. Naturally this adds more code to decode and execute which is really not OK in my book, especially for recursive code like the one in question.

There is a [C# language proposal](https://github.com/dotnet/csharplang/blob/master/proposals/skip-localsinit.md) that seems to be dormant about allowing developers to get around this weirdness, but in the meantime I devoted 5 minutes of my life to use the excellent [`LocalsInit.Fody`](https://github.com/ltrzesniewski/LocalsInit.Fody) weaver for [Fody](https://github.com/ltrzesniewski/LocalsInit.Fody) which can re-write assemblies to get rid of this annoyance. I encourage you to support Fody through open-collective as it really is a wonderful project that serves so many backbone projects in the .NET World.

At any rate, since we have lots of locals, and when we are after all implementing a recursive algorithm, this has a substantial effect on our code:



Not bad: a 1%-3% improvement (especially for higher array sizes) across the board for practically doing nothing...

### Selecting a good `InsertionSort` threshold: :+1:

As mentioned before, it's not clear that the currently selected threshold for `InsertionSort`, which is 16 elements + 1 (for pivot), or 17 in total is the best for our mix of partitioning. I tried 24, 32, 40, 48 on top of 16, and this is what came out in the form of an Area Bump Chart:

<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/insertion-sort-threshold.svg"></object>
This sort of visualization is great at showing us at each exact value of N, which threshold performed best, or was positioned as the lowest in the chart.  
What's abundantly clear is that 16 isn't cutting it. And while for small/medium sized arrays 40/48 looks like a good threshold, in my eyes, the clear overall winner here is 32, as it seems to perform pretty well for the smaller array sizes, and exceedingly well for larger arrays. 

### Aligning to CPU Cache-lines: :+1:

Alignment, generally speaking, is not critical in modern x64 processors, although some people believe in this myth, probably due to bad experience a decade or two ago. Here's a short excerpt from a recent edition of Intel System's Programming guide about this topic: 

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/cacheline-boundaries.svg"></object>
What we should be careful about, however, is when our read operations ends up **crossing** cache-lines (which are 64 bytes long on almost all modern HW). This literally causes the CPU to issue *two* read operations directed at the cache units. This sort of cache-line crossing read does have a sustained effect on perfromance[^0].  
For example, when we process an array using 4 byte reads, this means that when our data is *not* 4-bytes aligned these cross cache-line reads would occur at a rate of 4/64 or 6.25% of reads. In general, even this rate of cross-cacheline reads rarely even happens since in programming languages, C# included, both the default memory allocator and the JIT/Compiler work in tandem to avoid this situations by always aligning to machine word size on allocation, and adding padding within our classes and structs to make sure the different members are also aligned to 4/8 bytes.  
So far, I’ve told you why/when you shouldn’t care about this. This was my way of easing into the topic and providing the reasoning why most developers can afford *not to know* this and not pay any performance penalty. For the most part, this is simply a non-issue.  
Unfortunately, this is not true for `Vector256<T>` sized reads, which are 32 bytes wide (256 bits / 8). And this is doubly not true for our partitioning problem:

* Thee memory given to us to partition is almost *never* aligned to 32-bytes, since the allocator doesn’t care about that sort of alignment

* And even if it did, it would do us no good: once we perform a single partition operation, the following partitions’ start addresses are determined by the actual (random) data. There is no way we will get lucky enough that every partition will be magically 32-byte aligned.

Now that it is clear that we won’t be aligned to 32-bytes, this really means that when we go over the array sequentially (left to right and right to left as we do) we end up reading across cache-lines every **other** read! Or at a rate of 50%! Not cool.

Let’s consider our memory access patterns when reading/writing with respect to alignment:

* For writing, we're all over the place, we always advance the write pointers according to how the data was partitioned, e.g. it is data dependent, and there is little we can say about our write addresses. Also, Intel CPUs have a specific optimization for this in the form of store buffers, so the bottom line is we can’t/don't need to care about writing.
* For reading, the situation is different: We *always* advance the read pointers by 8 elements (32-bytes) on the one hand, and we actually have a special intrinsic: `Avx.LoadAlignedVector256()`[^1] that can help us ensure that we are indeed reading from aligned memory.

Can we do something about these cross-cacheline reads? We sure can! and initially, not at a great cost: remember that we need to deal with the remainder of the array anyway, and we've been doing that towards the end of our partitioning code thus far. We can move that code from the end of our partitioning function, to the beginning while also modifying it to partition with scalar code until both `readLeft`/`readRight` pointers are aligned to 32 bytes.  
This means we would do a little more scalar work:

* Previously we had 0-7 elements left as a remainder for scalar partitioning per partition call.
  * 3.5 elements on average.
* By aligning from the partition outer rims *inwards* we will have 0-7 elements on each side to partition with scalar code...
  * 7 elements on average.

In other words, this is an optimization with a trade-off: We will end up with more scalar work than before on the one hand (which is unfortunate), but we can change all of the loading code to use `Avx.LoadAlignedVector256()` and *know for sure* that we will no longer be causing the CPU to do cross cache-line reads (The latter being the performance boost).

I won't bother showing the entire code listing for `AVX2DoublePumpedAligned` , it's available [here](https://github.com/damageboy/QuicksortAvaganza/blob/master/VxSort/AVX2DoublePumpedAligned.cs), but I will show the rewritten scalar partition block; originally it was right after the double-pumped loop and looked like this:

```csharp
    while (readLeft < readRight) {
        var v = *readLeft++;

        if (v <= pivot) {
            *tmpLeft++ = v;
        } else {
            *--tmpRight = v;
        }
    }
```

The aligned variant, with the alignment logic now at the top of the function looks like this:

```csharp
    const ulong ALIGN = 32;
    const ulong ALIGN_MASK = ALIGN - 1;

    if (((ulong) readLeft & ALIGN_MASK) != 0) {
        var nextAlign = (int *) (((ulong) readLeft + ALIGN) & ~ALIGN_MASK);
        while (readLeft < nextAlign) {
            var v = *readLeft++;
            if (v <= pivot) {
                *tmpLeft++ = v;
            } else {
                *--tmpRight = v;
            }
        }
    }
    Debug.Assert(((ulong) readLeft & ALIGN_MASK) == 0);

    if (((ulong) readRight & ALIGN_MASK) != 0) {
        var nextAlign = (int *) ((ulong) readRight & ~ALIGN_MASK);
        while (readRight > nextAlign) {
            var v = *--readRight;
            if (v <= pivot) {
                *tmpLeft++ = v;
            } else {
                *--tmpRight = v;
            }
        }                
    }
    Debug.Assert(((ulong) readRight & ALIGN_MASK) == 0);
```

What it does now is check if alignment is necessary, and then proceeds to align while partitioning each side.

Where do we end up performance wise with this optimization? (to be clear, these results are compared to the latest a baseline version that now uses `32` as the `InsertionSort` threshold):

| Method                   | N        |     Mean (ns) | Time / N (ns) | Ratio |
| ------------------------ | -------- | ------------: | ------------: | ----: |
| AVX2DoublePumpedMicroOpt | 100      |         895.9 |        8.9585 |  1.00 |
| AVX2DoublePumpedAligned  | 100      |       2,879.6 |       28.7956 |  3.21 |
| AVX2DoublePumpedMicroOpt | 1000     |      19,093.2 |       19.0932 |  1.00 |
| AVX2DoublePumpedAligned  | 1000     |      25,468.0 |       25.4680 |  1.34 |
| AVX2DoublePumpedMicroOpt | 10000    |     278,365.7 |       27.8366 |  1.00 |
| AVX2DoublePumpedAligned  | 10000    |     272,146.9 |       27.2147 |  0.98 |
| AVX2DoublePumpedMicroOpt | 100000   |   2,806,369.0 |       28.0637 |  1.00 |
| AVX2DoublePumpedAligned  | 100000   |   2,614,231.4 |       26.1423 |  0.93 |
| AVX2DoublePumpedMicroOpt | 1000000  |  24,250,413.3 |       24.2504 |  1.00 |
| AVX2DoublePumpedAligned  | 1000000  |  23,771,945.4 |       23.7719 |  0.98 |
| AVX2DoublePumpedMicroOpt | 10000000 | 266,767,552.8 |       26.6768 |  1.00 |
| AVX2DoublePumpedAligned  | 10000000 | 258,396,465.8 |       26.4285 |  0.97 |

I know it does not seem like the most impressive improvement, but we somehow managed to speed up the function by around 2% while doubling the amount of scalar work done! This means that the pure benefit from alignment is larger than what the results are showing right now since it's being masked, to some extent, by the extra scalar work we tacked on. If only there was a way we could skip that scalar work all together...

### (Re-)Partitioning overlapping regions: :+1:

This is a very cool optimization and a natural progression from the last one. At the risk of sounding pompous, I think I *might* have found something here that no-one has done before in the context of partitioning. I could be wrong about that last statement, but I couldn't find anything quite like this discussed anywhere, and believe me, I've searched. If anyone can point me out to someone doing this before, I'd really love to hear about it, there might be more good stuff to read about there.

The basic idea here is we get rid of all (ok, ok, **almost all**) scalar partitioning in our vectorized code path. If we can partition and align the edges of the segment we are about to process with vectorized code, we would be reducing the total number instructions executed. At the same time, we would be retaining more of the speed-up that was lost with the alignment optimization we did before. This would have a double-whammy compounded effect. But how? 

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/overlap-partition-with-hint.svg"></object>
We could go about it the other way around! Instead of aligning *inwards* in each respective direction, we could align ***outwards*** and enlarge the partitioned segment to include a few more (up to 7) elements on the outer rims of each partition and <u>re-partition</u> them using the new pivot we've just selected. This might *sound simple* and **safe** but before we give it the OK, there are critical constraints we must respect for this not to completely mess up the entire sort mechanics.

Make note, that this is a very awkward optimization when you consider that I'm suggesting we should **partition more data** in order to *speed up* our code. This sounds bonkers, unless we dig deep within ourselves and find some mechanical empathy: We need to remind ourselves that not all work is equal in the eyes of the CPU. When we are doing scalar partitioning on *n* elements, we are really telling the CPU to execute *n* branches, which are completely data-dependent. To put it simply: The CPU "hates" this sort of work. It has to guess what happens next, and will do so no better than flipping a coin, so at a success rate of roughly 50% for truly random data. What's worse, as mentioned before, in the end of part 3, whenever the CPU mis-predicts, there's a huge penalty to pay in the form of a full pipeline flush which roughly costs us 14-15 cycles on a modern CPU. Paying this penalty **once**, is roughly equivalent to partitioning 2 x 8 element vectors in full with our branch-less vectorized partition block! This is the reason that doing "more" work might be faster. It's because what we think is more is actually less, when we empathize and understand the CPU.

Back to the constraints though: There's one thing we can **never** do, and that is move a pivot that was previously partitioned, I fondly call them "buried pivots" (since they're in their final resting place, get it?); as everyone knows, you don't move around dead bodies, that's always the first bad thing that happens in a horror movie. That's about it. It sounds simple, but it requires explanation: When a previous partition operation is complete, the pivot used during that operation will be moved to its final resting place in the sorted array. Moreover, all further partitioning operations will have their left/right edges calculated according to that final position of the pivot. We can not, under any circumstances ever move an already placed/buried pivot.  
So: Buried pivots need to stay buried where we left them, or bad things happen.

When we call our partitioning operation, we have to consider what initially looks like an asymmetry of the left and right edges of our to-be-partitioned segment:

* For the left side:

  * There might not be additional room on the left to read from for an 8-element partition operation!
    * In other words, we are too close to the edge of the array on the left side!
  * Since we always partition first to the left, then to the right, we know for a fact that all of elements to our left are completely sorted. e.g. they are all buried pivots, and we can't move them.
  * *Important:* We also know that each of those values is smaller than or equal to whatever pivot value we will select for our own partitioning operation.

* For the right side:

  * There might not be additional room on the right to read from for an 8-element partition operation!
    - In other words, we are too close to the edge of the array on the right side!
  * The immediate value to our right side is a pivot, and all other values to its right are larger-than-or-equal to it. So we can't move it with respect to its position.
  * There might be additional pivots immediately to our right as well.
  * *Important:* We also know that each of those values is larger-then-or-equal to whatever pivot value we select for our own partitioning operation.

All this information is hard to integrate at first, but what it boils down to is that we need to be very careful when we partition, or more accurately when we permute our data in the vectorized partition block. We need permutation entries that are "stable". I'm coining this phrase freely as I'm going along: we need to make sure our permutation table entries are stable on the left and stable on the right: e.g. they cannot reorder the values that need to go on the left amongst themselves (we have to keep their internal ordering amongst themselves), and they cannot reorder the values that need to go on the right amongst themselves.

Up to this point, there was no such requirement, and the initial partition tables I generated failed to satisfy this requirement.

Here's a simple example for an stable/unstable permutation entries, let's imagine we compared to a pivot value of 500:

| Bit                        | 0    | 1    | 2    | 3    | 4    | 5    | 6    | 7    |
| -------------------------- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| Value                      | 99   | 100  | 666  | 101  | 102  | 777  | 888  | 999  |
| Mask                       | 0    | 0    | 1    | 0    | 0    | 1    | 1    | 1    |
| Unstable<br />Permutation | 0    | 1    | **7** | 2    | 3    | **6** | **5** | **4** |
| Unstable Result     | 99 | 100 | 101 | 102 | **999** | **888** | **777** | **666** |
| Stable<br />Permutation | 0    | 1    | 4    | 2    | 3    | 5    | 6    | 7    |
| Stable Result | 99 | 100 | 101 | 102 | 666 | 777 | 888 | 999 |

In the above example, the unstable permutation is a perfectly *<u>valid</u>* permutation, and it successfully partitions the sample vector around the pivot value of 500, but the 4 elements I marked in bold are re-ordered with respect to each other, when compared to the original array; In the stable permutation entry, the internal ordering amongst the partitioned groups is *preserved*.

After I rewrote the code that generates the permutation entries, I proceeded with my overlapping re-partitioning hack: The idea was that I would find the optimal alignment point on the left and on the right (assuming one was available, e.g. there was enough room on that side) and read that data with our good ole `LoadVectorAligned256` intrinsic, then partition that data into the temporary area. But there is one additional twist: We need to remember how many elements do not belong to this partition (e.g. originate from our overlap hack) and remember not to copy them back at the end of the function.  
As long as we re-partition this extra data with our new stable permutation entries and remember ho much data to ignore on each side of the temporary memory, we’re good to go.


#### Sub-optimization- Converting branches to arithmetic: :+1:

By this time, my code contained quite a few branches to deal with various edge cases around alignment, and I pulled another rabbit out of the optimization hat that is worth mentioning: We can convert simple branches into arithmetic operations.

Many times, we end up having badly predicted branches with super simple code behind them, here's a real example I used to have in my code:

```csharp
int leftAlign;
...
if (leftAlign < 0) {
    readLeft += 8;
}
```

This looks awfully friendly, and it is, unless `leftAlign` and therefore the entire branch is determined by random data we read from the array, making the CPU mis-predict this branch at an alarming rate.  
The good news is that we can re-write this, entirely in C#, and replace the potential mis-prediction with a constant, predictable (and shorter!) data dependency. Let's start by inspecting the re-written "branch":

```csharp
int leftAlign;
...
// Signed arithmetic FTW
var leftAlignMask = leftAlign >> 31;
// the mask is now either all 1s or all 0s depending if leftAlign was negative/postive
readLeft += 8 & leftALignMask;
```

That's it! This turns out to be a quite effective way, again, for simple branches, at converting a 50% mis-prediction event costing us 15 cycles, with a 100% constant 3-4 cycles data-dependency for the CPU: It cannot complete the `readLeft +=` statement without waiting for the right-shift (`>> 31`) and the bitwise  and (`&`) operation to complete.  This is by no means the only/last time we will discuss this pattern in this optimization effort, but this is the first case where it worked out well for me, so I though I'm mention this and introduce the concept at the correct moment. This sort of technique is quite common in high-perf code, but it's use is limited to very simple branches: As the code inside the branch grows, the added cost of doing so many arithmetic/logic operation becomes a burden that ultimately unwinds any performance we might have gained from dropping the mis-predicted branch.

Anyway, back to the alignment story: The full code for this is actually quite hairy and I won't describe it in any more detail here, but brave souls are more than welcome to stare into the abyss, it was nice knowing you!

This is probably the time to inspect the results off all this new overlapping scalar-less code and show that while it cost me quite a few good night of pulling hairs groking horrible text dumps, this definitely did pay off:



There is an important caveat to describe about these results, though:

* The performance improvements are not spread evenly through-out the size of the sorting problem.

* I've conveniently included a vertical marker, per machine model, that shows the size of the L3 cache translated to elements.

* It can be clearly seen that as long as we're trying to sort within the size of our L3 cache, this optimization pays in spades: we're seeing around 20% reduction in runtime!

* As the problem size goes beyond the size of the cache, optimizing for L1/L2/L3 cross cache-line reads is meaningless as we are hit with the latency of RAM. As service to the reader here is a table of [latency numbers for a Skylake-X CPU](https://www.7-cpu.com/cpu/Skylake_X.html) running at 3 Ghz we should all keep in mind:

  | Event              | Cycles |   ns | Humanized                |
  | ------------------ | -----: | ---: | ------------------------ |
  | L1 cache read      |      4 |  1.3 | One heart beat (0.5 s)   |
  | Branch mis-predict |     14 |  4.6 | Yawn                     |
  | L2 cache read      |     14 |  4.6 | Yawn                     |
  | L3 cache read      |     68 | 22.6 | A correct ristretto pull |
  | Main memory read   |    229 |   76 | Brushing your teeth      |
  
  The humanized column makes it clear that it is ridiculous to consider optimizing yawns when we're wasting time brushing teeth all day long. 

### Prefetching: :-1:

I tried using prefetch intrinsics to give the CPU early hints as to where we are reading memory from.

Generally speaking prefetching should be used to make sure the CPU always reads some data from memory to cache ahead of the actual time we would use it so that the CPU never stalls waiting for memory which is very slow (Consult the table above again to get your bearings straight). The bottom line is that having to wait for RAM is a death sentence, but even having to wait for L2 cache (14 cycles) when your entire loop's throughput is around 6-7 cycles is really unpleasant. With prefetch intrinsics we can prefetch all the way to L1 cache, or even specify the target level as L2, L3.
But do we actually need to prefetch? Well, there is no clear cut answer except than trying it out. CPU designers know all of the above just as much as we do, and the CPU already attempts to prefetch data. But it's very hard to know when it might need our help. Adding prefetching instructions puts more load on the CPU as we're adding more instructions to decode & execute, while the CPU might already be doing the same work without us telling it. This is the key consideration we have to keep in mind when trying to figure out if prefetching is a good idea. To make matters worse, the answer can also be CPU model specific...  In our case, prefetching the *writable* memory **made no sense**, as our loop code mostly reads from the same addresses just before writing to them in the next iteration or two, so I mostly focused on trying to prefetch the next read addresses.

Whenever I modified `readLeft`, `readRight`, I immediately added code like this:

```csharp
int * nextPtr;
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight)) {
    nextPtr = readLeft;
    readLeft += 8;
    // Trying to be clever here,
    // If we are reading from the left at this iteration, 
    // we are likely to read from right in the next iteration
    Sse.Prefetch0((byte *) readRight - 64);
} else {
    nextPtr = readRight;
    readRight -= 8;
    // Same as above
    Sse.Prefetch0((byte *) readLeft + 64);
}
```

This tells the CPU we are about to use data in `readLeft + 64` (the next cache-line from the left) and `readRight -  64` (the next cache-line from the right) in the following iterations.

While this looks great on paper, the real world results of this were unnoticeable for me and even slightly negative. I think this is related to the fact that for the 2 CPUs I was trying this on, the prefetching unit in the CPU was already doing well without my generous help...  
Still it was worth a shot. 

### Packing the Permutation Table: :-1:

This following attempt yielded mixed results. In some cases (e.g. specific CPU models) it did slightly better, on others it did worse, but all in all I still think it's interesting that it didn't do worse overall, and I haven't given up on it completely.

The original permutation table is taking up 32 bytes per entry x 2<sup>8</sup> elements ⮞ 8kb in total. Just to be clear: **that's a lot!** For comparison, our entire CPU L1 data-cache is normally 32kb, and I'd sure rather have it store the actual data we're sorting, rather than my lookup table, right?

Well, not all is lost. We can do something semi-clever here, this will take the lookup table down to 4 bytes per element, or 8x improvement.

How?

Well, with intrinsics of course, if nothing else, it was worth it so I could do this:

![Yo Dawg](../assets/images/yodawg.jpg)

My optimized permutation table and vector loading code looks like this:

```csharp
ReadOnlySpan<byte> BitPermTable => new byte[]
{
    0b10001000, 0b11000110, 0b11111010, 0b00000000, // 0
    // ...
    0b01100011, 0b01111101, 0b01000100, 0b00000000, // 7    
    // ...
    0b00010000, 0b10011101, 0b11110101, 0b00000000, // 170
    // ...
    0b10001000, 0b11000110, 0b11111010, 0b00000000, // 255
}

Vector256<int> GetBitPermutation(uint *pBase, in uint mask)
{
    const ulong magicMask =
        0b00000111_00000111_00000111_00000111_00000111_00000111_00000111_00000111;
    return Avx2.ConvertToVector256Int32(
        Vector128.CreateScalarUnsafe(
            Bmi2.X64.ParallelBitDeposit(pBase[mask], magicMask)).AsByte());
}

```

What does this little monstrosity do exactly? We **pack** the permutation bits (remember, we just need 3 bits per element, we have 8 elements, so 24 bits per permutation vector in total) into a single 32 bit value, then whenever we need to permute, we:

* Unpack the 32-bit values into a 64-bit value using [`ParallelBitDeposit`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=pdep&expand=1532,4152) from the `BMI2` intrinsics extensions.  
  In a stroke of luck I've already throughly covered it back in my `PopCount` series [here]({% post_url 2018-08-19-netcoreapp3.0-intrinsics-in-real-life-pt2 %}).
* Convert (move) it to a 128-bit SIMD register using `Vector128.CreateScalarUnsafe`.
* Use yet another cryptic intrinsic [`ConvertToVector256Int32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_cvtepi8_epi32&expand=1532) (`VPMOVSXBD`) that takes 8-bit elements from a 128-bit wide register and expands them into integers in a 256 bit registers.

In short, we chain 3 extra instructions, but save 7KB of cache. Was it worth it?  
I wish I could say with a complete and assured voice that it was, but the truth is that it had only very little effect. While we end up using 1kb of cache instead of 8kb, the extra instructions still cost us quite a lot more. I still think this optimization might do some good, but for this to make a bigger splash we need to be in a situation were there is more pressure on the cache, for the extra latency to be worth it. I will refer to this in the last post, when I discuss other types of sorting that I would still need/want to support. Where I also think this packing approach might still prove useful...

### Skipping some permutations: :-1:

There are very common cases where permutation (and loading the permutation vector) is completely un-needed, to be exact there are exactly 8 such cases in the permutation table, whenever the all the `1` bits are already grouped in the upper (MSB) part of the register:

* 0b00000000
* 0b11111110
* 0b11111100
* 0b11111000
* 0b11110000
* 0b11100000
* 0b11000000
* 0b10000000

I thought it might be a good idea to detect those cases using a switch case or some sort of other intrinsics based code, while it did work, the extra branch and associated branch mis-prediction didn't make this worth while or yield any positive result. The simpler code which always permutes did just as good. Oh well, it was worth the attempt...

### Reordering instructions: :-1:

I also tried reordering some instructions so that they would happen sooner inside the loop body. For example: moving the `PopCount`ing to happen sooner (immediately after we calculate the mask).

None of these attempts helped, and I think the reason is that CPU already does this on its own, so while it sounds logical that this should happen, it doesn't seem to help when we change the code to do it given that the CPU already does it all by itself without our generous help.

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
