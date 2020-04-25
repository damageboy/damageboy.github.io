---
title: "This Goes to Eleven (Pt. 5/∞)"
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
date: 2020-02-02 05:22:28 +0300
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

### Aligning to CPU Cache-lines: :+1:

In modern computer hardware, CPUs *might* access memory more efficiently when it is naturally aligned: in other words, when the *address* we use is a multiple of some magical constant. This constant is classically the machine word size, which is 4/8 bytes on 32/64 bit machines. These constants are normally related to how the CPU is physically wired and constructed internally. While this is the generally accepted definition of alignment, with truly modern (read: expensive) CPUs, these requirements have become increasingly relaxed: Historically, older processors used to be very limited, either disallowing or severly limiting performance, with non-aligned access. To this day, very simple micro-controllers (like the ones you might find in IoT devices, for example) will exhibit such limitations around memory alignment, essentially forcing memory access to conform to multiples of 4/8.  
Going back to CPUs that are more relevant in the context of vectorized code (e.g. Intel/AMD CPUs like you are most probably using), most programmers can simply afford to *ignore* this issue. The last decade or so worth of modern processors are oblivious to this problem per-se, as long as we access memory within a **single cache-line**, or 64-bytes on almost any modern-day processors.

What is this cache-line? I'm actively fighting my internal inclination, so I **won't  turn** this post into a detour about computer micro-architecture. Besides, caches have been covered elsewhere ad-nauseaum by far more talanted writers, that I'll never do it justice anyway. Instead I'll just do the obligatory single paragraph reminder where we recall that CPUs don't directly communicate with RAM, as it is too slow; instead they read and write from internal, on-die, special/fast memory called caches. Caches are faster, smaller, and organized in multiple levels (L1/L2/L3 caches, to name them), where each level is usually larger in size and slightly slower in terms of latency. When the CPU is instructed to access memory, it instead communicates with the cache units, but it never does so in small units, even if our code is reading a single byte. Each processor comes with its definition of a minimal cache read/write unit, called a cache-line. Coincidentally, since this is, perhaps, the single most ironed out micro-arichtectural design issue with CPUs, it should come as no surprise that almost all modern CPUs, regardless of their manufacturer, seem to have converged to very similar cache designs and cache-line definitions: magically, almost all modern day hardware use 64-bytes as that golden number.


What happens when, lets say, our read operations end up **crossing** cache-lines?

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/cacheline-boundaries.svg"></object>

Such reads literally cause the CPU to issue *two* read operations directed at the load ports/cache units. This sort of cache-line crossing read can have a sustained effect on perfromance[^0]. But does it, Really? Let's consider it by way of example:   
Imagine we are processing a single array sequentially, reading 32-bit integers at a time, or 4-bytes; if for some reason, our starting address is *not* divisable by 4, cross cache-line reads would occur at a rate of `4/64` or `6.25%` of our reads. Even this pretty small rate of cross-cacheline access usually remains in the *realm of theory* since we have the memory allocator and compiler working in tandem, behind the scenes to make this go away:
* The default allocator always returns memory aligned at least to machine word size on the one hand.
* The compiler/JIT will use padding bytes within our classes/structs in-between members, where needed, to make sure that individual members are also aligned to 4/8 bytes.  

So far, I’ve told you why/when you *shouldn’t* care about alignment. This was my way of both easing you into the topic and helping you feel OK if this is news to you. You really can afford *not to think* about this and not pay any performance penalty, for the most part.  
Unfortunately, this stops being true for `Vector256<T>` sized reads, which are 32 bytes wide (256 bits / 8). And this is doubly not true for our partitioning problem:
* The memory given to us for partitioning/sorting is almost *never* aligned to 32-bytes, except for dumb luck, since the allocator doesn’t care about 32-byte alignment.
* Even if it were aligned, it would do *us* little good: The allocator, at best, would align the **entire** array to 32 bytes, but once we've performed a single partition operation, the next sub-division, inherent with QuickSort, would be determined by the actual (e.g. random) data. There is no way we will get lucky enough that every partition will be 32-byte aligned.

Now that it is clear that we won’t be aligned to 32-bytes, we can finally understand that when we go over the our array sequentially (left to right and right to left as we do) issuing **unaligned** 32-byte reads on top of a 64-byte cache-line, we end up reading across cache-lines every **other** read! Or at a rate of 50%! This just escalated from being "...generally not a problem" into a "Houston, we have a problem" very quickly.

Fine, we have a problem, the first step is acknowleding/accepting reality, so I'm told. Let’s consider our memory access patterns when reading/writing with respect to alignment:

* For writing, we're all over the place, we always advance the write pointers according to how the data was partitioned, e.g. it is completely data dependent, and there is little we can say about our write addresses. Also, as it happens, Intel CPUs, as almost all other modern CPUs employ another common trick in the form of [store buffer, or write-combining buffers (WCBs)](https://en.wikipedia.org/wiki/Write_combining), which I'll refrain from describing here; the bottom line is we both can’t/don't need to care about the writing side of our algorithm.
* For reading, the situation is entirely different: We *always* advance the read pointers by 8 elements (32-bytes) on the one hand, and we actually have a special intrinsic: `Avx.LoadAlignedVector256() / VMOVDQA`[^1] that helps us ensure that our reading is aligned to 32-bytes.

Can something be done about these cross-cacheline reads? Yes! and initially, I did get "something" working quickly: remember that we needed to deal with the remainder of the array anyway, and in the code presented at the end of the 3<sup>rd</sup> post, we had code doing just that at the end of our partitioning function. If we move that code from the end of the function to its beginning while modifying it to partition with scalar code until both `readLeft`/`readRight` pointers are aligned to 32 bytes, everything will become aligned. This does mean, however, we would do more scalar work:

* Previously we had anywhere between `0-7` elements left as a remainder for scalar partitioning per partition call.
  * `3.5` elements on average.
* Aligning from the outer rims *inwards* means we will have `0-7` elements per-side to partition with scalar code...
  * So `3.5 x 2 == 7` elements on average.

In other words, doing this sort of pre-alignment inwards is an optimization with a trade-off: We will end up with more scalar work than before on the one hand (which is unfortunate), but on the other hand, we can change the vector loading code to use `Avx.LoadAlignedVector256()` and *know for sure* that we will no longer be causing the CPU to issue a single cross cache-line read (The latter being the performance boost).  
I understand if your gut reaction is that adding 3.5 scalar operations doesn't sound like much of a trade off, but that would be an understatement:
* Each scalar comparison comes with a likely branch mis-prediction, as we discussed before, so it has a higher cost than what you might be initially pricing in
* Just as importantly: we can't forget that this is a recursive function, with ever *decreasing* partition sizes. If you go back to the initial stats we collected in previous posts, you'll be quickly reminded that we partition upwards of 340k times for 1 million element arrays, so this scalar work does pile up...

I won't bother showing the entire code listing for [`02_DoublePumpAligned.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/02_DoublePumpAligned.cs), but I will show the rewritten scalar partition block; originally it was right after the double-pumped loop and looked like this:

```csharp
    // ... 
    while (readLeft < readRight) {
        var v = *readLeft++;

        if (v <= pivot) {
            *tmpLeft++ = v;
        } else {
            *--tmpRight = v;
        }
    }
```

The aligned variant, with the alignment code now at the top of the function looks like this:

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

What it does now is check if alignment is necessary, and then proceeds to align while also partitioning each side into the temporary memory.

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

I know it does not seem like the most impressive improvement, but we somehow managed to speed up the function by around 2% while doubling the amount of scalar work done! This means that the pure benefit from alignment is larger than what the results are showing right now since it's being masked, to some extent, by the extra scalar work we tacked on. If only there was a way we could skip that scalar work all together... If only there was a way...

### (Re-)Partitioning overlapping regions: :+1:

This is a very cool optimization and a natural progression from the last one. At the risk of sounding pompous, I think I *might* have found something here that no-one has done before in the context of partitioning. I could be wrong about that last statement, but I couldn't find anything quite like this discussed anywhere, and believe me, I've searched. If anyone can point me out to someone doing this before, I'd really love to hear about it, there might be more good stuff to read about there.

The basic idea here is we get rid of all (ok, ok, **almost all**) scalar partitioning in our vectorized code path. If we can partition and align the edges of the segment we are about to process with vectorized code, we would be reducing the total number instructions executed. At the same time, we would be retaining more of the speed-up that was lost with the alignment optimization we did before. This would have a double-whammy compounded effect. But how? 

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/overlap-partition-with-hint.svg"></object>
We could go about it the other way around! Instead of aligning *inwards* in each respective direction, we could align ***outwards*** and enlarge the partitioned segment to include a few more (up to 7) elements on the outer rims of each partition and <u>re-partition</u> them using the new pivot we've just selected. If this works we will end up doing both 100% aligned reads and eliminating all scalar work in one optimization! This might *sound simple* and **safe** but this is exactly the sort of humbling experience that QuickSort is quick at dispensing (sorry, I had to) at people trying to nudge it in the wrong way. At some point I was finally able to screw my own head on properly with respect to this re-partitioning attempt and figure out what are exactly the critical constraints we must respect for this to work.

<table style="margin-bottom: 0em">
<tr>
<td style="border: none; padding-top: 0; padding-bottom: 0; vertical-align: top"><span class="uk-label">Note</span></td>
<td style="border: none; padding-top: 0; padding-bottom: 0"><div markdown="1">
This is a slightly awkward optimization when you consider that I'm suggesting we should **partition more data** in order to *speed up* our code. This sounds bonkers, unless we dig deep within ourselves and find some mechanical empathy: We need to remind ourselves that not all work is equal in the eyes of the CPU. When we are doing scalar partitioning on *n* elements, we are really telling the CPU to execute *n* branches, which are completely data-dependent. To put it simply: The CPU "hates" this sort of work. It has to guess what happens next, and will do so no better than flipping a coin, so at a success rate of roughly 50% for truly random data. What's worse, as mentioned before, in the end of part 3, whenever the CPU mis-predicts, there's a huge penalty to pay in the form of a full pipeline flush which roughly costs us 14-15 cycles on a modern CPU. Paying this penalty **once**, is roughly equivalent to partitioning 2 x 8 element vectors in full with our branch-less vectorized partition block! This is the reason that doing "more" work might be faster. It's because what we think is more is actually less, when we empathize and understand the CPU.
</div>
</td>
</tr>
</table>
{: .notice--info}

Back to the constraints though: There's one thing we can **never** do: move a pivot that was previously partitioned, I (now) call them "buried pivots" (since they're in their final resting place, get it?); as everyone knows, you don't move around dead bodies, that's always the first bad thing that happens in a horror movie, so there's our motivation: not being the stupid person who dies first. That's about it. It sounds simple, but it requires some more serious explanation: When a previous partition operation is complete, the pivot used during that operation will be moved to its final resting place in the sorted array and its position will be returned. All of those positions for the buried pivots are stored througout numerous call stacks of our recursive function and immediately used as the point around which we subdivide the partitioning process. In essence, except for the first call to the partitioning function, all further partitioning have their left/right edges calculated according to some previous buried pivot. So if we intened to **re-partition** data to the left and right of a given partition, we need to consider that this extra data might already contain buried pivots, and we can not, under any circumstances ever move an already placed/buried pivot.  
In short: Buried pivots stay buried where we left them, or bad things happen.

When we call our partitioning operation, we have to consider what initially looks like an asymmetry of the left and right edges of our to-be-partitioned segment:

* For the left side:
  * There might not be additional room on the left with extra data to read from.
    * In other words, we are too close to the edge of the array on the left side!  
      Of course this happens for all partitions starting at the left-edge of the entire array.
  * Since we always partition first to the left, then to the right, we know for a fact that 100% of elements left of "our" partition at any given moment are completely sorted. e.g. they are all buried pivots, and we can't move them.
  * *Important:* We also know that each of those values is smaller than or equal to whatever pivot value we *will select* for the current partitioning operation.

* For the right side, it is almost the same set of constraints:
  * There might not be additional room on the right with extra data to read from.
    * In other words, we are too close to the edge of the array on the right side!  
      Again, this naturally happens for all partitions ending on the right-edge of the entire array.
  * The immediate value to our right side is a pivot, and all other values to its right are larger-than-or-equal to it. So we can't move it with respect to its position.
  * There might be additional pivots immediately to our right as well.
  * *Important:* We also know that each of those values is larger-then-or-equal to whatever pivot value we *will select* for the current partitioning operation.

All this information is hard to integrate at first, but what it boils down to is that whenever we load up the left overlapping vector, there are anywhere between 1-7 elements we are **not** allowed to reorder on the *left side*, and when we load the right overlapping vector, there are, again, anywhere between 1-7 elements we are **not** allowed to re-order on *that right side*. That's the challenge; the good news is that all those overlapping elements are also guaranteed to also be smaller/larger than whatever pivot we end up selecting from out original (sans overlap) partition. This puts us in an interesting position: We know in advance that the extra elements will generate predictable comaprison results compared to *any* pivot within our partition. What I ended up doing was almost zen like: We "only" need permutation entries that are "stable". I'm coining this phrase freely as I'm going along: we need to make sure our permutation table entries are stable on the left and stable on the right: e.g. they **cannot** *reorder* the values that need to go on the left amongst themselves (we have to keep their internal ordering as-is amongst themselves), and they cannot reorder the values that need to go on the right amongst themselves. If we manage to do so, we're in the clear: The combination of stable pemutation and of predictable comparison results basically means that the overlapping elements will stay put, while all other elemens will be partitioned properly on both edge of our overlapping partition. If we succeed in doing that, we just need to forget we ever read those extra elements, and the whole thing just... works? ... yes!

Let's start with cementing this idea of what stable parittioning is: Up to this point, there was no such requirement, and the initial partition tables I generated failed to satisfy this requirement.
Here's a simple example for stable/unstable permutation entries, let's imagine we compared to a pivot value of 500:

| Bit                        | 0    | 1    | 2    | 3    | 4    | 5    | 6    | 7    |
| -------------------------- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| `Vector256<T>` Value       | 99   | 100  | 666  | 101  | 102  | 777  | 888  | 999  |
| Mask                       | 0    | 0    | 1    | 0    | 0    | 1    | 1    | 1    |
| Unstable Permutation       | 0    | 1    | **7** | 2    | 3    | **6** | **5** | **4** |
| Unstable Result            | 99 | 100 | 101 | 102 | **999** | **888** | **777** | **666** |
| Stable Permutation         | 0    | 1    | 4    | 2    | 3    | 5    | 6    | 7    |
| Stable Result              | 99 | 100 | 101 | 102 | 666 | 777 | 888 | 999 |

In the above example, the unstable permutation is a perfectly *<u>valid</u>* permutation for general case partitioning, and it successfully partitions the sample vector around the pivot value of 500, but the 4 elements I marked in bold are re-ordered with respect to each other, when compared to the original array; In the stable permutation entry, the internal ordering amongst the partitioned groups is *preserved*.

After I rewrote the code that generates the permutation entries, I proceeded with my overlapping re-partitioning hack: The idea was that I would find the optimal alignment point on the left and on the right (assuming one was available, e.g. there was enough room on that side) and read that data with our good ole `LoadVectorAligned256` intrinsic, then partition that data into the temporary area. But there is one additional twist: We need to remember how many elements *do not belong* to this partition (e.g. originate from our overlap hack) and remember not to copy them back at the end of the function. To my amazement, that was kind of it. It just works! (I've conveniently ignored a small edge-cases here in words, but not in the code :).

The end result is super delicate. To be clear: I've just described how I partition the initial 2x8 elements (8 on each side); out of those initial 8, I *always* have a subset I must **never** reorder, and a subset I need to re-order, as is normal in partitioning, with respect to some pivot. By relying on our knowledge about what *possible* pivot value *might* be selected and how it compares to the subset I wish to keep stuck on each edge on the one hand while also relying on my newly minted stable permutation entries to not reorder those extra elements, on the other hand, I literally get to eat my cake and keep it whole: For the 99% case we **KILL** scalar partitioning all-together, literally doing *zero* scalar work, at the same time aligning everything to `Vector256<T>` size and being nice to our processor. Just to make this victory a tiny touch sweeter, even our *initial* 2x8 reads used for the alignment itself are aligned reads! I don't know about you, but usually my life is not filled with such joy... So this, understanably, made me quite happy.

The final alignment through overlapping partitioning (which I called "overligned" in my code-base), is available in full in [`03_DoublePumpOverlined.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/03_DoublePumpOverlined.cs). It implements this overlapping alignment approach, with some extra small points for consideration:
* It detects when it is **impossible** to align outwards and fallsback to the alignment mechanic we introduced in the previous section.  
  This is pretty uncommon: Going back to the statistical data we collected about random-data sorting in the 1<sup>st</sup> post, we anticipate a recursion depth of around 40 when sorting 1M elements and ~340K partitioning calls. This means we will have *at least* 40x2 (for both sides) such cases where we are forced to align inwards for that 1M case, as an example.  
  This is small change compared to the `340k - 80` calls we can optimize with outward alignment, but it does mean we have to keep that old code lying around.
* Once we calculate for a given partition how much alignment is required on each side, we can re-use that calculation recursively for the entire depth of the recursive call stack: This again reduces the amount of alignment calculations by a factor of 40x for 1M elements, as an example.  
  In the code you'll see I'm squishing two 32-bit integers into a 64-bit value I call `alignHint` and I keep reusing one half of 64-bit value without recalculating the alignment *amount*; I've made it this far, let's shave a few more cycles off while we're here.
* This is a good time as any to remind our-selves that we also read `Vector256<T>` sized permutation entries from memory, and those are just as likely to be unaligned 32-bytes and cause superfluous cache traffic, so the code uses a static initializer to re-align that memory as well.
  * Unlike with partitioning, this is done by allocating memory and copying the table around.
  * Given that our permutation table, at this stage, is 8KB, or two pages worth of RAM/cache, I've decided to align it to 4KB rather than 32 bytes: The reasoning behind this is to make sure 8KB worth of entries use EXACTLY two pages worth of virtual addresses rather than 3. This reduces the amount of [TLB entries](https://en.wikipedia.org/wiki/Translation_lookaside_buffer) (yet another cache in the processor I'm going to name drop and not bother to explain).
  This is a very minor optimization, but heck, why not?

#### Sub-optimization- Converting branches to arithmetic: :+1:

By this time, my code contained quite a few branches to deal with various edge cases around alignment, and I pulled another rabbit out of the optimization hat that is worth mentioning: We can convert simple branches into arithmetic operations.  
C/C++/Rust/Go developers who are used to standing on the shoulders of giants (referring to the LLVM compiler which powers a lot of hyper-optimized code-bases here) might look at this with puzzlemenmt, but this is an old geezer trick that comes in handy since the C# JIT isn't smart enough to this for us at the time I'm writing this.

Many times, we end up having branches with super simple code behind them, here's a real example I used to have in my code, as part of some early version of overlinement:

```csharp
int leftAlign;
...
if (leftAlign < 0) {
    readLeft += 8;
}
```

This looks awfully friendly, and it is, unless `leftAlign` and therefore the entire branch is determined by random data we read from the array, making the CPU mis-predict this branch at an alarming rate.  
The good news is that we can re-write this, entirely in C#, and replace the potential mis-prediction with a constant, predictable (and often shorter!) data dependency. Let's start by inspecting the re-written "branch":

```csharp
int leftAlign;
...
// Signed arithmetic FTW
var leftAlignMask = leftAlign >> 31;
// the mask is now either all 1s or all 0s depending if leftAlign was negative/postive
readLeft += 8 & leftALignMask;
```

That's it! This turns out to be a quite effective way, again, for simple branches, at converting a potential mis-prediction event costing us 15 cycles, with a 100% constant 3-4 cycles data-dependency for the CPU: It can be thought as a "signaling" mechanism where we tell the CPU not to speculate on the result of the branch but instead complete the `readLeft +=` statement only after waiting for the right-shift (`>> 31`) and the bitwise and (`&`) operation to complete. I referred to this as an old geezer's optimization since modern processors already support this internally in the form of a `CMOV` instruction, which is more versatile, faster and takes up less bytes in the instruction stream while having the same "do no speculte on this" effect on the CPU. The only issue is that we don't have that available to us in the C#/CoreCLR JIT (I think that Mono's JIT, pecuiliarly does support this both with the internal JIT and naturally with LLVM). As a side note, I'll point out that this is such an old-dog trick that LLVM can even detect such code and de-optimize it back into a "normal" branch and then proceed to optimize it again into `CMOV`, which I think is just a very cool thing :)

If I'm completely honest, I'm not sure why exactly using this branch to branchless trick even had an effect on the performance of the partitioning function, since these branches should be super easy to predict. I ended up replacing about 5-6 super simple/small branches this way, and while I have my suspicions, I do not know for sure how doing this at the top of the `VectorizeInPlace` function helped by an extra 1-2%. Since we're already talking real numbers, it's probably a good time to show where we end up with the entire overlined version:


BIG TABLE GOES HERE!!!!



This is great! I chose to compare this to the micro-optimized version rather than the previous aligned version, since both of them revolve around the same basic idea. Getting a 15-20% bump across the board like this is nothing to snicker at!
There is an important caveat to mention about these results, though:
* The performance improvements are not spread evenly through-out the size of the sorting problem.
* I've conveniently included a vertical marker, per machine model, that shows the size of the L3 cache translated to # of elements.
  * It can be clearly seen that as long as we're sorting roughly within the size of our L3 cache, this optimization pays in spades: we're seeing around 20% reduction in runtime!
  * As the problem size goes beyond the size of the cache, optimizing for L1/L2/L3 cross cache-line reads is meaningless as we are hit with the latency of RAM. As service to the reader here is a table of [latency numbers for a Skylake-X CPU](https://www.7-cpu.com/cpu/Skylake_X.html) running at 3 Ghz we should all keep in mind:

  | Event              | Cycles |   ns | Humanized                |
  | ------------------ | -----: | ---: | ------------------------ |
  | L1 cache read      |      4 |  1.3 | One heart beat (0.5 s)   |
  | Branch mis-predict |     14 |  4.6 | Yawn                     |
  | L2 cache read      |     14 |  4.6 | Yawn                     |
  | L3 cache read      |     68 | 22.6 | A correct ristretto pull |
  | Main memory read   |    229 |   76 | Brushing your teeth      |
  
  The humanized column makes it clear that it is ridiculous to consider optimizing yawns when we're wasting time brushing teeth all day long. 

* The last thing I should probably mention is that I still ended up leaving a few pennies on the floor here: When I partition into the temporary space, I could have done so in such a way that by the time I go back to reading that data as part of copying it back, I could make sure that *those* final reads would also end up being aligned to `Vector256<T>`. I didn't bother doing so, because I think it would have very marginal effects as the current method for copying back the temporary memory is probably already fast enough. I doubt that replacing `Unsafe.CopyUnalignedBlock` with some hand rolled AVX2 copying code would be greatly beneficial here.

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

---

[^0]: Most modern Intel CPUs can actually address the L1 cache units twice per cycle, that means they can actually ask it to read two cache-line as the same time. But this still causes more load on the cache and bus, and we must not forget that we will be reading an additional cache-line for our permutation block...
[^1]: This specific AVX2 intrinsic will actually fail if/when used on non-aligned addresses. But it is important to note that it seems it won’t actually run faster than the previous load intrinsic we’ve used: `AVX2.LoadDquVector256` as long as the actual addresses we pass to both instructions are 32-byte aligned. In other words, it’s very useful for debugging alignment issues, but not that critical to actually call that intrinsic! 
