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
date: 2020-02-02 05:22:28 +0300
classes: wide
chartjs:
  scales:
    xAxes:
      - scaleLabel:
          display: true,
          labelString: "N (elements)"
          fontFamily: "Indie Flower"
        ticks:
          fontFamily: "Indie Flower"

  legend:
    display: true
    position: bottom
    labels:
      fontFamily: "Indie Flower"
      fontSize: 14
  title:
    position: top
    fontFamily: "Indie Flower"
    fontSize: 16

#categories: coreclr intrinsics vectorization quicksort sorting

---

I ended up going down the rabbit hole re-implementing array sorting with AVX2 intrinsics, and there's no reason I should go down alone.

Since there’s a lot to go over here, I’ll split it up into a few parts:

1. In [part 1]({% post_url 2020-01-28-this-goes-to-eleven-pt1 %}), we start with a refresher on `QuickSort` and how it compares to `Array.Sort()`.
2. In [part 2]({% post_url 2020-01-29-this-goes-to-eleven-pt2 %}), we go over the basics of vectorized hardware intrinsics, vector types, and go over a handful of vectorized instructions we’ll use in part 3. We still won't be sorting anything.
3. In [part 3]({% post_url 2020-01-30-this-goes-to-eleven-pt3 %}), we go through the initial code for the vectorized sorting, and start seeing some payoff. We finish agonizing courtesy of the CPU’s branch predictor, throwing a wrench into our attempts.
4. In [part 4]({% post_url 2020-02-01-this-goes-to-eleven-pt4 %}), we go over a handful of optimization approaches that I attempted trying to get the vectorized partition to run faster, seeing what worked and what didn't.
5. In this part, we'll take a deep dive into how to deal with memory alignment issues.
6. In part 6, we’ll take a pause from the vectorized partitioning, to get rid of almost 100% of the remaining scalar code, by implementing small, constant size array sorting with yet more AVX2 vectorization.
7. In part 7, We'll circle back and try to deal with a nasty slowdown left in our vectorized partitioning code
8. In part 8, I'll tell you the sad story of a very twisted optimization I managed to pull off while failing miserably at the same time.
9. In part 9, I'll try some algorithmic improvements to milk those last drops of perf, or at least those that I can think of, from this code.

## (Trying) to squeeze some more vectorized juice

I thought it would be nice to show a bunch of things I ended up trying to improve performance.
I tried to keep most of these experiments in separate implementations, both the ones that yielded positive results and the failures. These can be seen in the original repo under the [Happy](https://github.com/damageboy/VxSort/tree/research/VxSortResearch/Unstable/AVX2/Happy) and [Sad](https://github.com/damageboy/VxSort/tree/research/VxSortResearch/Unstable/AVX2/Sad) folders.

While some worked, and some didn't, I think a bunch of these were worth mentioning, so here goes:

### Do we have an alignment problem?

With modern computer hardware, CPUs *might* access memory more efficiently when it is naturally aligned: in other words, when the *address* we use is a multiple of some magical constant. The constant is classically the machine word size, 4/8 bytes on 32/64 bit machines. These constants are related to how the CPU is physically wired and constructed internally. Historically, older processors used to be very limited, either disallowing or severely limiting performance, with non-aligned memory access. To this day, very simple micro-controllers (like the ones you might find in IoT devices, for example) will exhibit such limitations around memory alignment, essentially forcing memory access to conform to multiples of 4/8. With more modern (read: more expensive) CPUs, these requirements have become increasingly relaxed. Most programmers can simply afford to *ignore* this issue. The last decade or so worth of modern processors are oblivious to this problem per-se, as long as we access memory within a **single cache-line**, or 64-bytes on almost any modern-day processors.

What is this cache-line? I'm actively fighting my internal inclination, so I **won't  turn** this post into a detour about computer micro-architecture. Caches have been covered elsewhere ad-nauseam by far more talented writers, that I'll never do it justice anyway. Instead, I'll just do the obligatory one-paragraph reminder where we recall that CPUs don't directly communicate with RAM, as it is dead slow; instead they read and write from internal, on-die, special/fast memory called caches. Caches contain partial copies of RAM. Caches are faster, smaller, and organized in multiple levels (L1/L2/L3 caches, to name them), where each level is usually larger in size and slightly slower in terms of latency. When the CPU is instructed to access memory, it instead communicates with the cache units, but it never does so in small units. Even when our code is reading a *single byte*, the CPU will communicate with it's cache subsystem in a unit-of-work known as a cache-line. In theory, every CPU model may have its own definition of a cache-line, but in practice, the last 15 years of processors seem to have converged on 64-bytes as that golden number.

Now, what happens when, lets say, our read operations end up **crossing** cache-lines?

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/cacheline-boundaries.svg"></object>

As mentioned, the unit-of-work, as far as the CPU is concerned, is a 64-byte cache-line. Therefore, such reads literally cause the CPU to issue *two* read operations downstream, directed at the cache units. These cache-line crossing reads *do* have a sustained effect on perfromance[^0]. But how often do they occur? Let's consider this by way of example:  
Imagine we are processing a single array sequentially, reading 32-bit integers at a time, or 4-bytes; if for some reason, our starting address is *not* divisible by 4, cross cache-line reads would occur at a rate of `4/64` or `6.25%` of reads. Even this paltry rate of cross cache-line reads usually remains in the *realm of theory* since we have the memory allocator and compiler working in tandem, behind the scenes, to make this go away:

* The default allocator *always* returns memory aligned at least to machine word size on the one hand.
* The compiler/JIT use padding bytes within our classes/structs in-between members, as needed, to ensure that individual members are aligned to 4/8 bytes.  

So far, I’ve told you why/when you *shouldn’t* care about alignment. This was my way of both easing you into the topic and helping you feel OK if this is news to you. You really can afford *not to think* about this without paying any penalty, for the most part. Unfortunately, this **stops** being true for `Vector256<T>` sized reads, which are 32 bytes wide (256 bits / 8). And this is *doubly not true* for our partitioning problem:

* The memory handed to us for partitioning/sorting is rarely aligned to 32-bytes, except for dumb luck.  
  The allocator, allocating an array of 32-bit integers simply doesn’t care about 32-**byte** alignment.
* Even if it were magically aligned to 32-bytes, it would do us little good; Once a *single* partition operation is complete, further sub-divisions, inherent with QuickSort, are determined by the (random) new placement of the last pivot we used.  
  There is no way we will get lucky enough that *every partition* will be 32-byte aligned.

Now that it is clear that we won’t be 32-byte aligned, we finally realize that as we go over the array sequentially (left to right and right to left as we do) issuing **unaligned** 32-byte reads on top of a 64-byte cache-line, we end up reading across cache-lines every **other** read! Or at a rate of 50%! This just escalated from being "...generally not a problem" into a "Houston, we have a problem" very quickly.

You've endured through a lot of hand waving so far, let's try to see if we can get some damning evidence for all of this, by launching `perf`, this time tracking the oddly specific `mem_inst_retired.split_loads` HW counter:

```bash
$ COMPlus_PerfMapEnabled=1 perf record -Fmax -e mem_inst_retired.split_loads \
    ./Example --type-list DoublePumpJedi --size-list 100000 \
        --max-loops 1000 --no-check
$ perf report --stdio -F overhead,sym | head -20

# To display the perf.data header info, please use --header/--header-only options.
# Event count (approx.): 87102613
# Overhead  Symbol 
    86.68%  [.] ...DoublePumpJedi::VectorizedPartitionInPlace(int32*,int32*)
     5.74%  [.] ...DoublePumpJedi::Sort(int32*,int32*,int32)
     2.99%  [.] __memmove_avx_unaligned_erms
```

We ran the same sort operation 1,000 times and got `87,102,613` split-loads, with `86.68%` attributed to our partitioning function. This means `(87102613 * 0.8668) / 1000` or `75,500` split-loads *per sort* of `100,000` elements. To seal the deal, we need to figure out how many vector loads per sort we are performing in the first place; Luckily I can generate an answer quickly: I have statistics collection code embedded in my code, so I can issue this command:

```bash
$ ./Example --type-list DoublePumpJedi \
      --size-list 100000 --max-loops 10000 \
      --no-check --stats-file jedi-100k-stats.json
```

And in return I get this beutiful thing back:

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>

<table class="table datatable"
  data-json="../_posts/jedi-stats.json"
  data-id-field="name"
  data-pagination="false"
  data-intro="Each row in this table contains statistics collected & averaged out of thousands of runs with random data" data-position="left"
  data-show-pagination-switch="false">
  <thead data-intro="The header can be used to sort/filter by clicking" data-position="right">
    <tr>
        <th data-field="MethodName" data-sortable="true"
            data-filter-control="select">
          <span
              data-intro="The name of the benchmarked method"
              data-position="top">Method<br/>Name</span>
        </th>
        <th data-field="ProblemSize" data-sortable="true"
            data-value-type="int"
            data-filter-control="select">
            <div data-intro="The size of the sorting problem being benchmarked (# of integers)"  data-position="bottom" class="rotated-header-container">
            <div class="rotated-header">Size</div>
            </div>
        </th>
        <th data-field="MaxDepthScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="The maximal depth of recursion reached while sorting"  data-position="top" class="rotated-header-container">
              <div class="rotated-header">Max</div>
              <div class="rotated-header">Depth</div>
            </div>
        </th>
        <th data-field="NumPartitionOperationsScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="# of partitioning operations per sort" data-position="bottom" class="rotated-header-container">
              <div class="rotated-header">Part</div>
              <div class="rotated-header">itions</div>
            </div>
        </th>
        <th data-field="NumVectorizedLoadsScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="# of vectorized load operations" data-position="top" class="rotated-header-container">
              <div class="rotated-header">Vector</div>
              <div class="rotated-header">Loads</div>
            </div>
        </th>
        <th data-field="NumVectorizedStoresScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="# of vectorized store operations" data-position="bottom" class="rotated-header-container">
              <div class="rotated-header">Vector</div>
              <div class="rotated-header">Stores</div>
            </div>
        </th>
        <th data-field="NumPermutationsScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="# of vectorized permutation operations" data-position="top" class="rotated-header-container">
              <div class="rotated-header">Vector</div>
              <div class="rotated-header">Permutes</div>
            </div>
        </th>
        <th data-field="AverageSmallSortSizeScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="For hybrid sorting, the average size that each small sort operation was called with (e.g. InsertionSort)"
                 data-position="bottom" class="rotated-header-container">
              <div class="rotated-header">Small</div>
              <div class="rotated-header">Sort</div>
              <div class="rotated-header">Size</div>
            </div>
        </th>
        <th data-field="NumScalarComparesScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <div data-intro="How many branches were executed in each sort operation that were based on the unsorted array elements"
                 data-position="top" class="rotated-header-container">
              <div class="rotated-header">Data</div>
              <div class="rotated-header">Based</div>
              <div class="rotated-header">Branches</div>
            </div>
        </th>
        <th data-field="PercentSmallSortCompares" data-sortable="true"
            data-value-type="float2-percentage">
            <div data-intro="What percent of</br>⬅<br/>branches happenned as part of small-sorts"
              data-position="bottom" class="rotated-header-container">
              <div class="rotated-header">Small</div>
              <div class="rotated-header">Sort</div>
              <div class="rotated-header">Branches</div>
            </div>
        </th>
    </tr>
  </thead>
</table>
</div>

In total, we perform `173,597` vector loads per sort operation of `100,000` elements in `4,194` partitioning calls. Every partitioning call has a `4/32` or `12.5%` of ending up being 32-byte aligned: In other words `21700` of the total vector reads should be aligned by sheer chance, which leaves `173597-21700` or `151,898` that should be *unaligned*, of which, I claim that that ½ would cause split-loads: `50%` of `151,898` is `75,949` while we measured `75,500`! I don't know how your normal day goes about, but in mine, reality and my hallucinations rarely go hand-in-hand like this.

Fine, we now **know** we have a problem. The first step was acknowledging/accepting reality: Our code does indeed generate a lot of split memory operations. Let’s consider our memory access patterns when reading/writing with respect to alignment, and see if we can do something about it:

* For writing, we're all over the place: we always advance the write pointers according to how the data was partitioned, e.g. it is completely data-dependent, and there is little we can say about our write addresses. In addition, as it happens, Intel CPUs, as almost all other modern CPUs, employ another common trick in the form of [store buffers, or write-combining buffers (WCBs)](https://en.wikipedia.org/wiki/Write_combining). I'll refrain from describing them here, but the bottom line is we both can’t/don't need to care about the writing side of our algorithm.
* For reading, the situation is entirely different: We *always* advance the read pointers by 8 elements (32-bytes) on the one hand, and we even have a special intrinsic: `Avx.LoadAlignedVector256() / VMOVDQA`[^1] that helps us ensure that our reading is properly aligned to 32-bytes.

<table style="margin-bottom: 0em">
<tr>
<td style="border: none; padding-top: 0; padding-bottom: 0; vertical-align: top"><span class="uk-label">Note</span></td>
<td style="border: none; padding-top: 0; padding-bottom: 0"><div markdown="1">
For completness sake, I should mention that alignment with vectorized code was not always such a free-spirited beast. Until recently, using aligned reads was mandatory. With AVX2, we can swing it both ways, and to be technically accurate, what really matters, is that the *address* we use is aligned, rather than us using this-or-that type of instruction.
As such, my motivation for using `Avx.LoadAlignedVector256() / VMOVDQA` can be explained by conveniently having an instruction that reads from aligned addresses and will automatically fault when I've been naughty.
</div>
</td>
</tr>
</table>
{: .notice--info}

#### Aligning to CPU Cache-lines, aligning inwards: :-1:

With this long introduction out of the way, it's time we do something about these cross-cache line reads. Initially, I got "something" working quickly: remember that we needed to deal with the *remainder* of the array, when we had less than 8-elements, anyway. In the original code at the end of the 3<sup>rd</sup> post, we did so right after out vectorized loop. If we move that scalar code from the end of the function to its beginning while also modifying it to perform scalar partitioning until both `readLeft`/`readRight` pointers are aligned to 32 bytes, our work is complete. There is a slight wrinkle in this otherwise simple approach:

* Previously, we had anywhere between `0-7` elements left as a remainder for scalar partitioning per partition call.
  * `3.5` elements on average.
* Aligning from the edges of our partition with scalar code means we will now have `0-7` elements per-side...
  * So `3.5 x 2 == 7` elements on average.

In other words, doing this sort of inwards pre-alignment optimization is not a clean win: We end up with more scalar work than before on the one hand (which is unfortunate), but on the other hand, we can change the vector loading code to use `Avx.LoadAlignedVector256()` and *know for sure* that we will no longer be causing the CPU to issue a single cross cache-line read (The latter being the performance boost).  
It's understandable if while reading this, your gut reaction is thinking that adding 3.5 scalar operations doesn't sound like much of a trade-off, but we have to consider that:

* Each scalar comparison comes with a likely branch misprediction, as discussed before, so it has a higher cost than what you might be initially pricing in.
* More importantly: we can't forget that this is a recursive function, with ever *decreasing* partition sizes. If you go back to the initial stats we collected in previous posts, you'll be quickly reminded that we partition upwards of 340k times for 1 million element arrays, so this scalar work both piles up, and represents a larger portion of our workload as the partition sizes decrease...

I won't bother showing the entire code listing for [`02_DoublePumpAligned.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/02_DoublePumpAligned.cs), but I will show the rewritten scalar partition block, which is now tasked with aligning our pointers before we go full vectorized partitioning. Originally it was right after the double-pumped loop and looked like this:

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

The aligned variant, with the alignment code now at the top of the function, looks like this:

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

BLA BLA TABLE GOES HERE.


I know it does not seem like the most impressive improvement, but we somehow managed to speed up the function by around 2% while doubling the amount of scalar work done! This means that the pure benefit from alignment is larger than what the results are showing right now since it's being masked, to some extent, by the extra scalar work we tacked on. If only there was a way we could skip that scalar work all together... If only there was a way...

### (Re-)Partitioning overlapping regions: :+1:

Next up is a very cool optimization and a natural progression from the last one. At the risk of sounding pompous, I think I *might* have found something here that no-one has done before in the context of partitioning[^2]: The basic idea here is we get rid of all (ok, ok, **almost all**) scalar partitioning in our vectorized code path. If we can partition and align the edges of the segment we are about to process with vectorized code, we would be reducing the total number instructions executed. At the same time, we would be retaining more of the speed-up that was lost with the alignment optimization above. This would have a double-whammy compounded effect. But how?

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/overlap-partition-with-hint.svg"></object>

We could go about it the other way around! Instead of aligning *inwards* in each respective direction, we could align ***outwards*** and enlarge the partitioned segment to include a few more (up to 7) elements on the outer rims of each partition and <u>re-partition</u> them using the new pivot we've just selected. If this works, we end up doing both 100% aligned reads and eliminating all scalar work in one optimization! This might *sound simple* and **safe**, but this is the sort of humbling experience that QuickSort is quick at dispensing (sorry, I had to...) at people trying to nudge it in the wrong way. At some point I was finally able to screw my own head on properly with respect to this re-partitioning attempt and figure out what precisely are the critical constraints we must respect for this to work.

<table style="margin-bottom: 0em">
<tr>
<td style="border: none; padding-top: 0; padding-bottom: 0; vertical-align: top"><span class="uk-label">Note</span></td>
<td style="border: none; padding-top: 0; padding-bottom: 0"><div markdown="1">
This is a slightly awkward optimization when you consider that I'm suggesting we should **partition more data** in order to *speed up* our code. This sounds bonkers, unless we dig deep within to find some mechanical empathy: We need to remind ourselves that not all work is equal in the eyes of the CPU. When we are doing scalar partitioning on *n* elements, we are really telling the CPU to execute *n* branches, comparisons, and memory accesses, which are completely data-dependent. To put it simply: The CPU "hates" this sort of work. It has to guess what happens next, and will do so no better than flipping a coin, so at a success rate of roughly 50% for truly random data. What's worse, as mentioned before, whenever the CPU mispredicts, there's a price to pay in the form of a full pipeline flush which roughly costs us 14-15 cycles on a modern CPU. Paying this **once**, is roughly equivalent to partitioning 2 x 8 element vectors in full with our branch-less vectorized partition block! This is the reason that doing "more" might be faster.
</div>
</td>
</tr>
</table>
{: .notice--info}

Back to the constraints. There's one thing we can **never** do: move a pivot that was previously partitioned. I (now) call them "buried pivots" (since they're in their final resting place, get it?); Everyone knows, you don't move around dead bodies, that's always the first bad thing that happens in a horror movie. There's our motivation: not being the stupid person who dies first. That's about it. It sounds simple, but it requires some more serious explanation: When a previous partition operation is complete, the pivot used during that operation is moved to its final resting place. It's new position is used to subdivide the array, and effectively stored throughout numerous call stacks of our recursive function. There's a baked-in assumption here, that all data left/right of that buried pivot is smaller/larger than it. And that assumption must **never** be broken. If we intend to **re-partition** data to the left and right of a given partition, as part of this overlapping alignment effort, we need to consider that this extra data might already contain buried pivots, and we can not, under any circumstances ever move them again.  
In short: Buried pivots stay buried where we left them, or bad things happen.

When we call our partitioning operation, we have to consider what initially looks like an asymmetry of the left and right edges of our to-be-partitioned segment:

* For the left side:
  * There might not be additional room on the left with extra data to read from.
    * In other words, we are too close to the edge of the array on the left side!  
      Of course this happens for all partitions starting at the left-edge of the entire array.
  * Since we always partition first to the left, then to the right, we know for a fact that 100% of elements left of "our" partition at any given moment are entirely sorted. e.g. they are all buried pivots, and we can't re-order them.
  * *Important:* We also know that each of those values is smaller than or equal to whatever pivot value we *will select* for the current partitioning operation.

* For the right side, it is almost the same set of constraints:
  * There might not be additional room on the right with extra data to read from.
    * In other words, we are too close to the edge of the array on the right side!  
      Again, this naturally happens for all partitions ending on the right-edge of the entire array.
  * The immediate value to our right side is a pivot, and all other values to its right are larger-than-or-equal to it. So we can't move it with respect to its position.
  * There might be additional pivots immediately to our right as well.
  * *Important:* We also know that each of those values is larger-then-or-equal to whatever pivot value we *will select* for the current partitioning operation.

All this information is hard to integrate at first, but what it boils down to is that whenever we load up the left overlapping vector, there are anywhere between 1-7 elements we are **not** allowed to reorder on the *left side*, and when we load the right overlapping vector, there are, again, anywhere between 1-7 elements we are **not** allowed to re-order on *that right side*. That's the challenge; the good news is that all those overlapping elements are also guaranteed to also be smaller/larger than whatever pivot we end up selecting from out original (sans overlap) partition. This knowledge gives us the edge we need: We know in advance that the extra elements will generate predictable comparison results compared to *any* pivot *within* our partition.

What we need are permutation entries that are ***stable***. I'm coining this phrase freely as I'm going along:  
Stable partitioning means that the partitioning operation **must not** *reorder* values that need to go on the left amongst themselves (we keep their internal ordering amongst themselves). Likewise, it **must not** reorder the values that go on the right amongst themselves. If we manage to do this, we're in the clear: The combination of stable permutation and predictable comparison results means that the overlapping elements will stay put while other elements will be partitioned properly on both edges of our overlapping partition. After this weird permutation, we just need to forget we ever read those extra elements, and the whole thing just... works? ... yes!

Let's start with cementing this idea of what stable partitioning is: Up to this point, there was no such requirement, and the initial partition tables I generated failed to satisfy this requirement.
Here's a simple example for stable/unstable permutation entries, let's imagine we compared to a pivot value of 500:

| Bit                        | 0    | 1    | 2    | 3    | 4    | 5    | 6    | 7    |
| -------------------------- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| `Vector256<T>` Value       | 99   | 100  | 666  | 101  | 102  | 777  | 888  | 999  |
| Mask                       | 0    | 0    | 1    | 0    | 0    | 1    | 1    | 1    |
| Unstable Permutation       | 0    | 1    | **7** | 2    | 3    | **6** | **5** | **4** |
| Unstable Result            | 99 | 100 | 101 | 102 | **999** | **888** | **777** | **666** |
| Stable Permutation         | 0    | 1    | 4    | 2    | 3    | 5    | 6    | 7    |
| Stable Result              | 99 | 100 | 101 | 102 | 666 | 777 | 888 | 999 |

In the above example, the unstable permutation is a perfectly *<u>valid</u>* permutation for general case partitioning. It successfully partition the sample vector around the pivot value of 500; but the 4 elements I marked in bold are re-ordered with respect to each other when compared to the original array. In the stable permutation entry, the internal ordering amongst the partitioned groups is *preserved*.

Armed with new permutation entries, I proceeded with my overlapping re-partitioning hack: The idea was that I would find the optimal alignment point on the left and on the right (assuming one was available, e.g. there was enough room on that side) and read that data with our good ole `LoadVectorAligned256` intrinsic, then partition that data into the temporary area. But there is one additional twist: We need to remember how many elements *do not belong* to this partition (e.g. originate from our overlap hack) and remember not to copy them back at the end of the function. To my amazement, that was kind of it. It just works! (I've conveniently ignored a small edge-cases here in words, but not in the code :).

The end result is super delicate. To be clear: I've just described how I partition the initial 2x8 elements (8 on each side); out of those initial 8, I *always* have a subset I must **never** reorder (the overlap), and a subset I need to re-order, as is normal in partitioning, with respect to some pivot. We know that whatever *possible* pivot value *might* be selected from our internal partition, it will always be larger/smaller than the elements in the overlapping areas. Knowing that, we can rely on having stable permutation entries that **do not** reorder those extra elements. We literally get to eat our cake and keep it whole: For the 99% case we **KILL** scalar partitioning all-together, literally doing *zero* scalar work, at the same time aligning everything to `Vector256<T>` size and being nice to our processor. Just to make this victory a tiny touch sweeter, even our *initial* 2x8 reads used for the alignment itself are aligned reads! I don't know about your life, but mine, is usually not filled with such joy... So this, understandably, made me quite happy.

The final alignment through overlapping partitioning (which I called "overligned" in my code-base), is available in full in [`03_DoublePumpOverlined.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/03_DoublePumpOverlined.cs). It implements this overlapping alignment approach, with some extra small points for consideration:

* It detects when it is **impossible** to align outwards and falls back to the alignment mechanic we introduced in the previous section.  
  This is pretty uncommon: Going back to the statistical data we collected about random-data sorting in the 1<sup>st</sup> post, we anticipate a recursion depth of around 40 when sorting 1M elements and ~340K partitioning calls. This means we will have *at least* 40x2 (for both sides) such cases where we are forced to align inwards for that 1M case, as an example.  
  This is small change compared to the `340k - 80` calls we can optimize with outward alignment, but it does mean we have to keep that old code lying around.
* Once we calculate for a given partition how much alignment is required on each side, we can re-use that calculation recursively for the entire depth of the recursive call stack: This again reduces the amount of alignment calculations by a factor of 40x for 1M elements, as an example.  
  In the code you'll see I'm squishing two 32-bit integers into a 64-bit value I call `alignHint` and I keep reusing one half of 64-bit value without recalculating the alignment *amount*; If we've made it this far, let's shave a few more cycles off while we're here.
* This is a good time as any to remind our-selves that we also read `Vector256<T>` sized permutation entries from memory, and those are just as likely to be unaligned 32-bytes and cause superfluous cache traffic, so the code uses a static initializer to re-align that memory as well.
  * Unlike with partitioning, this is done by allocating memory and copying the table around.
  * Given that our permutation table, at this stage, is 8KB, or two pages worth of RAM/cache, I've decided to align it to 4KB rather than 32 bytes: The reasoning behind this is to make sure 8KB worth of entries use EXACTLY two pages worth of virtual addresses rather than 3. This reduces the amount of [TLB entries](https://en.wikipedia.org/wiki/Translation_lookaside_buffer) (yet another cache in the processor I'm going to name drop and not bother to explain).
  This is a very minor optimization, but heck, why not?

#### Sub-optimization- Converting branches to arithmetic: :+1:

By this time, my code contained quite a few branches to deal with various edge cases around alignment, and I pulled another rabbit out of the optimization hat that is worth mentioning: We can convert simple branches into arithmetic operations.  
C/C++/Rust/Go developers who are used to standing on the shoulders of giants (referring to the LLVM compiler which powers a lot of hyper-optimized code-bases here) might look at this with puzzlement, but this is an old geezer trick that comes in handy since the C# JIT isn't smart enough to this for us at the time I'm writing this.

Many times, we end up having branches with super simple code behind them, here's a real example I used to have in my code, as part of some early version of overlinement:

```csharp
int leftAlign;
...
if (leftAlign < 0) {
    readLeft += 8;
}
```

This looks awfully friendly, and it is, unless `leftAlign` and therefore the entire branch is determined by random data we read from the array, making the CPU mispredict this branch at an alarming rate.  
The good news is that we can re-write this, entirely in C#, and replace the potential mis-prediction with a constant, predictable (and often shorter!) data dependency. Let's start by inspecting the re-written "branch":

```csharp
int leftAlign;
...
// Signed arithmetic FTW
var leftAlignMask = leftAlign >> 31;
// the mask is now either all 1s or all 0s depending if leftAlign was negative/postive
readLeft += 8 & leftALignMask;
```

That's it! This turns out to be a quite effective way, again, for simple branches, at converting a potential misprediction event costing us 15 cycles, with a 100% constant 3-4 cycles data-dependency for the CPU: It can be thought as a "signaling" mechanism where we tell the CPU not to speculate on the result of the branch but instead complete the `readLeft +=` statement only after waiting for the right-shift (`>> 31`) and the bitwise and (`&`) operation to complete. I referred to this as an old geezer's optimization since modern processors already support this internally in the form of a `CMOV` instruction, which is more versatile, faster and takes up less bytes in the instruction stream while having the same "do no speculate on this" effect on the CPU. The only issue is that we don't have that available to us in the C#/CoreCLR JIT (I think that Mono's JIT, peculiarly does support this both with the internal JIT and naturally with LLVM). As a side note, I'll point out that this is such an old-dog trick that LLVM can even detect such code and de-optimize it back into a "normal" branch and then proceed to optimize it again into `CMOV`, which I think is just a very cool thing :)

If I'm completely honest, I'm not sure why exactly using this branch to branchless trick even had an effect on the performance of the partitioning function, since these branches should be super easy to predict. I ended up replacing about 5-6 super simple/small branches this way, and while I have my suspicions, I do not know for sure how doing this at the top of the `VectorizeInPlace` function helped by an extra 1-2%. Since we're already talking real numbers, it's probably a good time to show where we end up with the entire overlined version:

BIG TABLE GOES HERE!!!!

This is great! I chose to compare this to the micro-optimized version rather than the previous aligned version, since both of them revolve around the same basic idea. Getting a 15-20% bump across the board like this is nothing to snicker at!


```bash
$ perf record -Fmax -e mem_inst_retired.split_loads \
   ./Example --type-list DoublePumpOvelined --size-list 100000 \
       --max-loops 1000 --no-check
$ perf report --stdio -F overhead,sym | head -20
# To display the perf.data header info, please use --header/--header-only options.
# Samples: 129  of event 'mem_inst_retired.split_loads'
# Event count (approx.): 12900387
# Overhead  Symbol 
    30.23%  [.] DoublePumpOverlined...::Sort(int32*,int32*,int64,int32)
    28.68%  [.] DoublePumpOverlined...::VectorizedPartitionInPlace(int32*,int32*,int64)
    13.95%  [.] __memmove_avx_unaligned_erms
     0.78%  [.] JIT_MemSet_End
```


We just split loads reduced by 95%, and our vectorized partitioning function is not even the first one in the list.
It seems like some form of reality is agreeing we did good here..


There is an important caveat to mention about these results, though:

* The performance improvements are not spread evenly through-out the size of the sorting problem.
* I've conveniently included a vertical marker, per machine model, that shows the size of the L3 cache translated to # of elements.
  * It can be clearly seen that as long as we're sorting roughly within the size of our L3 cache, this optimization pays in spades: we're seeing around 20% reduction in runtime!
  * As the problem size goes beyond the size of the cache, optimizing for L1/L2/L3 cross cache-line reads is meaningless as we are hit with the latency of RAM. As service to the reader here is a table of [latency numbers for a Skylake-X CPU](https://www.7-cpu.com/cpu/Skylake_X.html) running at 3 Ghz we should all keep in mind:

  | Event              | Cycles |   ns | Humanized                |
  | ------------------ | -----: | ---: | ------------------------ |
  | L1 cache read      |      4 |  1.3 | One heart beat (0.5 s)   |
  | Branch mispredict |     14 |  4.6 | Yawn                     |
  | L2 cache read      |     14 |  4.6 | Yawn                     |
  | L3 cache read      |     68 | 22.6 | A correct ristretto pull |
  | Main memory read   |    229 |   76 | Brushing your teeth      |
  
  The humanized column makes it clear that it is ridiculous to consider optimizing yawns when we're wasting time brushing teeth all day long.

* The last thing I should probably mention is that I still ended up leaving a few pennies on the floor here: When I partition into the temporary space, I could have done so in such a way that by the time I go back to reading that data as part of copying it back, I could make sure that *those* final reads would also end up being aligned to `Vector256<T>`. I didn't bother doing so, because I think it would have very marginal effects as the current method for copying back the temporary memory is probably already fast enough. I doubt that replacing `Unsafe.CopyUnalignedBlock` with some hand-rolled AVX2 copying code would be greatly beneficial here.

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

This is it! We ended up sneaking up a data-based branch into our code in the form of this side-selection logic. Whenever we try to pick a side, we would read from next this is where we put the CPU in a tough spot. We're asking it to speculate on something it *can't possibly speculate on successfully*. Our question is: "Oh CPU, CPU in the socket, Which side is closer to being over-written of them all?", to which the answer is completely data-driven! In other words, it depends on how the last round(s) of partitioning mutated all 4 pointers involved in the comparison. While it might sound like an easy thing for the CPU to check, we have to remember it is actually required to *speculate* this ahead of time, since every time the CPU is demanded to answer this question, it it is **still in the middle** of processing a few of the previous iterations of this very same hot-loop due to the length of the pipeline and the nature of speculative execution. So the CPU guesses, at best, on stale data, and we know, as the grand designers of this mess that in reality, at least for random data, the best guess is no better here than flipping a coin. Quite sad. You have to admit it is quite ironic how we managed to do this whole big circle around our own tails just to come-back to having a branch misprediction based on the random array data.

Mis-predicting here is unavoidable. Or at least I have no idea on how to avoid it in C# with the current JIT in August 2019 (But oh, just you wait for part 6, I have something in store there for you..., hint hint, wink wink).

But not all is lost.

#### Replacing the branch with arithmetic: :-1:

Could we replace this branch with arithmetic just like I showed a couple of paragraphs above?  Well, We could, except that it runs more slowly:

Consider this alternative version:

```chsarp

```

This code has a few effects:

* It make me want to puke
* It eliminates branch misprediction in our vectorized partitioning path almost entirely:
  * I measured < 5% misprediction with this
* It generates SO much additional code that its simply just not worth it!

So while this attempt seems futile for now, we see that it fails for the "wrong" reason. We **did** manage to eliminate the misprediction, it simply looks like the price is too high. This is again a mid-way conclusion I will get back to in a future post.

---

[^0]: Most modern Intel CPUs can actually address the L1 cache units twice per cycle, at least when it comes to reading data, by virtue of having two load-ports. That means they can actually request two cache-line as the same time! But this still causes more load on the cache and bus. In our case, we must also remember we will be reading an additional cache-line for our permutation entry...
[^1]: This specific AVX2 intrinsic will actually fail if/when used on non-aligned addresses. But it is important to note that it seems it won’t actually run faster than the previous load intrinsic we’ve used: `AVX2.LoadDquVector256` as long as the actual addresses we pass to both instructions are 32-byte aligned. In other words, it’s very useful for debugging alignment issues, but not that critical to actually call that intrinsic!
[^2]: I could be wrong about that last statement, but I couldn't find anything quite like this discussed anywhere, and believe me, I've searched. If anyone can point me out to someone doing this before, I'd really love to hear about it, there might be more good stuff to read about there...
