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

### Aligning our expectations

<center>
<object style="margin: auto; width: 90%" type="image/svg+xml" data="../assets/images/computer-architecture-caches-are-evil-quote.svg"></object>
</center>

This quote, taken from Hennessy and Patterson's ["Computer Architecture: A Quantitative Approach, 6th Edition"](https://www.elsevier.com/books/computer-architecture/hennessy/978-0-12-811905-1), which is traced to all the way back to the fathers of modern-day computing in 1946 can be taken as a foreboding warning for the pains that are related to anything that deals with the complexity of memory hierarchies.

With modern computer hardware, CPUs *might* access memory more efficiently when it is naturally aligned: in other words, when the *address* we use is a multiple of some magical constant. The constant is classically the machine word size, 4/8 bytes on 32/64 bit machines. These constants are related to how the CPU is physically wired and constructed internally. Historically, older processors used to be very limited, either disallowing or severely limiting performance, with non-aligned memory access. To this day, very simple micro-controllers (like the ones you might find in IoT devices, for example) will exhibit such limitations around memory alignment, essentially forcing memory access to conform to multiples of 4/8 bytes. With more modern (read: more expensive) CPUs, these requirements have become increasingly relaxed. Most programmers can simply afford to *ignore* this issue. The last decade or so worth of modern processors are oblivious to this problem per-se, as long as we access memory within a **single cache-line**, or 64-bytes on almost any modern-day processors.

What is this cache-line? I'm actively fighting my internal inclination, so I **won't  turn** this post into a detour about computer micro-architecture. Caches have been covered elsewhere ad-nauseam by far more talented writers, that I'll never do it justice anyway. Instead, I'll just do the obligatory one-paragraph reminder where we recall that CPUs don't directly communicate with RAM, as it is dead slow; instead, they read and write from internal, on-die, special/fast memory called caches. Caches contain partial copies of RAM. Caches are faster, smaller, and organized in multiple levels (L1/L2/L3 caches, to name them), where each level is usually larger in size and slightly slower in terms of latency. When the CPU is instructed to access memory, it instead communicates with the cache units, but it never does so in small units. Even when our code is reading a *single byte*, the CPU will communicate with it's cache subsystem in a unit-of-work known as a cache-line. In theory, every CPU model may have its own definition of a cache-line, but in practice, the last 15 years of processors seem to have converged on 64-bytes as that golden number.

Now, what happens when, lets say, our read operations end up **crossing** cache-lines?

<center>
<object style="margin: auto; width: 90%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/cacheline-boundaries.svg"></object>
</center>

As mentioned, the unit-of-work, as far as the CPU is concerned, is a 64-byte cache-line. Therefore, such reads literally cause the CPU to issue *two* read operations downstream, ultimately directed at the cache units[^0]. These cache-line crossing reads *do* have a sustained effect on perfromance[^1]. But how often do they occur? Let's consider this by way of example:  
Imagine we are processing a single array sequentially, reading 32-bit integers at a time, or 4-bytes; if for some reason, our starting address is *not* divisible by 4, cross cache-line reads would occur at a rate of `4/64` or `6.25%` of reads. Even this paltry rate of cross cache-line reads usually remains in the *realm of theory* since we have the memory allocator and compiler working in tandem, behind the scenes, to make this go away:

* The default allocator *always* returns memory aligned at least to machine word size on the one hand.
* The compiler/JIT use padding bytes within our classes/structs in-between members, as needed, to ensure that individual members are aligned to 4/8 bytes.  

So far, I’ve told you why/when you *shouldn’t* care about alignment. This was my way of both easing you into the topic and helping you feel OK if this is news to you. You really can afford *not to think* about this without paying any penalty, for the most part. Unfortunately, this **stops** being true for `Vector256<T>` sized reads, which are 32 bytes wide (256 bits / 8). And this is *doubly not true* for our partitioning problem:

* The memory handed to us for partitioning/sorting is rarely aligned to 32-bytes, except for dumb luck.  
  The allocator, allocating an array of 32-bit integers, simply doesn’t care about 32-**byte** alignment.
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

We ran the same sort operation `1,000` times and got `87,102,613` split-loads, with `86.68%` attributed to our partitioning function. This means `(87102613 * 0.8668) / 1000` or `75,500` split-loads *per sort* of `100,000` elements. To seal the deal, we need to figure out how many vector loads per sort we are performing in the first place; Luckily I can generate an answer quickly: I have statistics collection code embedded in my code, so I can issue this command:

```bash
$ ./Example --type-list DoublePumpJedi \
      --size-list 100000 --max-loops 10000 \
      --no-check --stats-file jedi-100k-stats.json
```

And in return I get this beutiful thing back:

<table style="margin-bottom: 0em">
<tr>
<td style="border: none; padding-top: 0; padding-bottom: 0; vertical-align: top"><span class="uk-label">Note</span></td>
<td style="border: none; padding-top: 0; padding-bottom: 0"><div markdown="1">
These numbers are vastly different than the ones we last saw in the end of the 3<sup>rd</sup> post, for example. There is a good reason for this: We've spent the previous post tweaking the code in a few considerable ways:
* Changing the cut-off point for vectorized sorting from 16 ⮞ 40, there-by reducing the amount of vectorized partitions we're performing in the first place.
* Changing the permutation entry loading code to read 8-byte values from memroy, rather than full 32-byte `Vector256<int>` entries,
  cutting the number of `Vector256<int>` loads by half.
</div>
</td>
</tr>
</table>
{: .notice--info}

<div>
<!-- <button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button> -->

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

In total, we perform `173,597` vector loads per sort operation of `100,000` elements in `4,194` partitioning calls. Assuming our array is aligned to 4-bytes to begin with (which C#'s allocator does very reliably), every partitioning call has a `4/32` or `12.5%` of ending up being 32-byte aligned: In other words `21,700` of the total vector reads should be aligned by sheer chance, which leaves `173597-21700` or `151,898` that should be *unaligned*, of which, I claim that that ½ would cause split-loads: `50%` of `151,898` is `75,949` while we measured `75,500` with `perf`! I don't know how your normal day goes about, but in mine, reality and my hallucinations rarely go hand-in-hand like this.

Fine, we now **know** we have a problem. The first step was acknowledging/accepting reality: Our code does indeed generate a lot of split memory operations. Let’s consider our memory access patterns when reading/writing with respect to alignment, and see if we can do something about it:

* For writing, we're all over the place: we always advance the write pointers according to how the data was partitioned, e.g. it is completely data-dependent, and there is little we can say about our write addresses. In addition, as it happens, Intel CPUs, as almost all other modern CPUs, employ another common trick in the form of [store buffers, or write-combining buffers (WCBs)](https://en.wikipedia.org/wiki/Write_combining). I'll refrain from describing them here, but the bottom line is we both can’t/don't need to care about the writing side of our algorithm.
* For reading, the situation is entirely different: We *always* advance the read pointers by 8 elements (32-bytes) on the one hand, and we even have a special intrinsic: `Avx.LoadAlignedVector256() / VMOVDQA`[^2] that helps us ensure that our reading is properly aligned to 32-bytes.

#### Aligning to CPU Cache-lines: :+1:

With this lengthy introduction out of the way, it's time we do something about these cross-cache line reads. Initially, I got "something" working quickly: remember that we needed to deal with the *remainder* of the array, when we had less than 8-elements, anyway. In the original code at the end of the 3<sup>rd</sup> post, we did so right after our vectorized loop. If we move that scalar code from the end of the function to its beginning while also modifying it to perform scalar partitioning until both `readLeft`/`readRight` pointers are aligned to 32 bytes, our work is complete. There is a slight wrinkle in this otherwise simple approach:

* Previously, we had anywhere between `0-7` elements left as a remainder for scalar partitioning per partition call.
  * `3.5` elements on average.
* Aligning from the edges of our partition with scalar code means we will now have `0-7` elements per-side...
  * So `3.5 x 2 == 7` elements on average.

In other words, doing this sort of inwards pre-alignment optimization is not a clean win: We end up with more scalar work than before on the one hand (which is unfortunate), but on the other hand, we can change the vector loading code to use `Avx.LoadAlignedVector256()` and *know for sure* that we will no longer be causing the CPU to issue a single cross cache-line read (The latter being the performance boost).  
It's understandable if while reading this, your gut reaction is thinking that adding 3.5 scalar operations doesn't sound like much of a trade-off, but we have to consider that:

* Each scalar comparison comes with a likely branch misprediction, as discussed before, so it has a higher cost than what you might be initially pricing in.
* More importantly: we can't forget that this is a recursive function, with ever *decreasing* partition sizes. If you go back to the initial stats we collected in previous posts, you'll be quickly reminded that we partition upwards of 340k times for 1 million element arrays, so this scalar work both piles up, and represents a larger portion of our workload as the partition sizes decrease...

I won't bother showing the entire code listing for [`B5_1_DoublePumpAligned.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/B5_1_DoublePumpAligned.cs), but I will show the rewritten scalar partition block, which is now tasked with aligning our pointers before we go full vectorized partitioning. Originally it was right after the double-pumped loop and looked like this:

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

What it does now is check when alignment is necessary, then proceeds to align while also partitioning each side into the temporary memory.

Where do we end up performance-wise with this optimization?

<div markdown="1">
<div class="stickemup">

{% codetabs %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Scaling %}
<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Performance scale: Array.Sort (solid gray) is always 100%, and the other methods are scaled relative to it" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,100K,1M,10M
Jedi,         1   , 1   , 1  , 1   , 1    , 1
Aligned, 1.082653616,    1.091733385,    0.958578753,    0.959159569,    0.964604818,    0.980102965
<!-- 
{ 
 "data" : {
  "datasets" : [
  { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3}
  },
  { 
    "backgroundColor": "rgba(33,220,33,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 60, "hachureGap": 3}
  }  
  ]
 },
 "options": {
    "title": { "text": "AVX2 Aligned Sorting - Scaled to Jedi", "display": true },
    "scales": { 
      "yAxes": [{
       "ticks": {
         "fontFamily": "Indie Flower",
         "min": 0.90, 
         "callback": "ticksPercent"
        },
        "scaleLabel": {
          "labelString": "Scaling (%)",
          "display": true
        }
      }]
    }
 },
 "defaultOptions": {{ page.chartjs | jsonify }}
}
--> </canvas>

</div>
</div>
</div>
</div>
</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Time/N %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Time in nanoseconds spent sorting per element. Array.Sort (solid gray) is the baseline, again" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,100K,1M,10M
Jedi, 18.3938  ,20.7342  ,24.6347  ,26.9067  ,23.9922  ,25.5122
Aligned, 19.9128, 22.6363, 23.6143, 25.8078, 23.143, 25.0046
<!-- 
{ 
 "data" : {
  "datasets" : [
  { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3}
  },
  { 
    "backgroundColor": "rgba(33,220,33,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 60, "hachureGap": 3}
  }
  ]
 },
 "options": {
    "title": { "text": "AVX2 Jedi Sorting + Aligned - log(Time/N)", "display": true },
    "scales": { 
      "yAxes": [{ 
        "type": "logarithmic",
        "ticks": {
          "min": 15,
          "max": 28,
          "callback": "ticksNumStandaard",
          "fontFamily": "Indie Flower"          
        },
        "scaleLabel": {
          "labelString": "Time/N (ns)",
          "fontFamily": "Indie Flower",
          "display": true
        }
      }]
    }
 },
 "defaultOptions": {{ page.chartjs | jsonify }}
}
--> </canvas>

</div>
</div>
</div>
</div>
</div>
{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-list-alt'></i> Benchmarks %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<table class="table datatable"
  data-json="../_posts/Bench.BlogPt5_1_Int32_-report.datatable.json"
  data-id-field="name"
  data-pagination="false"
  data-page-list="[9, 18]"
  data-intro="Each row in this table represents a benchmark result" data-position="left"
  data-show-pagination-switch="false">
  <thead data-intro="The header can be used to sort/filter by clicking" data-position="right">
    <tr>
        <th data-field="TargetMethodColumn.Method" data-sortable="true"
         data-filter-control="select">
          <span
              data-intro="The name of the benchmarked method"
              data-position="top">
            Method<br/>Name
          </span>
        </th>
        <th data-field="N" data-sortable="true"
            data-value-type="int" data-filter-control="select">
            <span
              data-intro="The size of the sorting problem being benchmarked (# of integers)"
              data-position="top">
            Problem<br/>Size
            </span>
        </th>
        <th data-field="TimePerNDataTable" data-sortable="true"
            data-value-type="float2-interval-muted">
            <span
              data-intro="Time in nanoseconds spent sorting each element in the array (with confidence intervals in parenthesis)"
              data-position="top">
              Time /<br/>Element (ns)
            </span>
        </th>
        <th data-field="RatioDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal-percentage">
            <span data-intro="Each result is scaled to its baseline (Array.Sort in this case)"
                  data-position="top">
                  Scaling
            </span>
        </th>
        <th data-field="Measurements" data-sortable="true" data-value-type="inline-bar-vertical">
            <span data-intro="Raw benchmark results visualize how stable the result it. Longest/Shortest runs marked with <span style='color: red'>Red</span>/<span style='color: green'>Green</span>" data-position="top">Measurements</span>
        </th>
    </tr>
  </thead>
</table>
</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-info-sign'></i> Setup %}

```bash
BenchmarkDotNet=v0.12.0, OS=clear-linux-os 32120
Intel Core i7-7700HQ CPU 2.80GHz (Kaby Lake), 1 CPU, 4 logical and 4 physical cores
.NET Core SDK=3.1.100
  [Host]     : .NET Core 3.1.0 (CoreCLR 4.700.19.56402, CoreFX 4.700.19.56404), X64 RyuJIT
  Job-DEARTS : .NET Core 3.1.0 (CoreCLR 4.700.19.56402, CoreFX 4.700.19.56404), X64 RyuJIT

InvocationCount=3  IterationCount=15  LaunchCount=2
UnrollFactor=1  WarmupCount=10

$ grep 'stepping\|model\|microcode' /proc/cpuinfo | head -4
model           : 158
model name      : Intel(R) Core(TM) i7-7700HQ CPU @ 2.80GHz
stepping        : 9
microcode       : 0xb4
```

{% endcodetab %}

{% endcodetabs %}
</div>

The whole attempt ends up as a mediocre improvement, so it would seem:

* We're are seeing a speedup/improvement, in the high counts.
* We seem to be slowing down due to the higher scalar operation count, in the low problem sizes.

It's kind of a mixed bad, and perhaps slightly unimpressive at first glance. However, when we stop to remember that we somehow managed both to speed up the function while doubling the amount of scalar work done, the interpretation of the results becomes more nuanced: The pure benefit from alignment itself is larger than what the results are showing right now since it's being masked, to some extent, by the extra scalar work we tacked on. If only there was a way we could skip that scalar work all together... If only there was a way... If only...
</div>

### (Re-)Partitioning overlapping regions: :+1: :+1:

Next up is a different optimization approach to the same problem, and a natural progression from the last one. At the risk of sounding pompous, I think I *might* have found something here that no-one has done before in the context of partitioning[^3]: The basic idea here is we get rid of all (ok, ok, *almost all*) scalar partitioning in our vectorized code path. If we can partition and align the edges of the segment we are about to process with vectorized code, we would be reducing the total number instructions executed. At the same time, we would be retaining more of the speed-up that was lost with the alignment optimization above. This would have a double-whammy compounded effect. But how?

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/overlap-partition-with-hint.svg"></object>

We could go about it the other way around! Instead of aligning *inwards* in each respective direction, we could align ***outwards*** and enlarge the partitioned segment to include a few more (up to 7) elements on the outer rims of each partition and <u>re-partition</u> them using the new pivot we've just selected. If this works, we end up doing both 100% aligned reads and eliminating all scalar work in one optimization! This might *sound simple* and **safe**, but this is the sort of humbling experience that QuickSort is quick at dispensing (sorry, I had to...) at people trying to nudge it in the wrong way. At some point, I was finally able to screw my own head on properly with respect to this re-partitioning attempt and figure out what precisely are the critical constraints we must respect for this to work.

<table style="margin-bottom: 0em">
<tr>
<td style="border: none; padding-top: 0; padding-bottom: 0; vertical-align: top"><span class="uk-label">Note</span></td>
<td style="border: none; padding-top: 0; padding-bottom: 0"><div markdown="1">
This is a slightly awkward optimization when you consider that I'm suggesting we should **partition more data** in order to *speed up* our code. This sounds bonkers, unless we dig deep within for some mechanical empathy: not all work is equal in the eyes of the CPU. When we are executing scalar partitioning on *n* elements, we are really telling the CPU to execute *n* branches, comparisons, and memory accesses, which are completely data-dependent. The CPU "hates" this sort of work. It has to guess what happens next, and will do so no better than flipping a coin, or 50%, for truly random data. What's worse, as mentioned before, whenever the CPU mispredicts, there's a price to pay in the form of a full pipeline flush which roughly costs us 14-15 cycles on a modern CPU. Paying this **once**, is roughly equivalent to partitioning 2 x 8 element vectors with our vectorized partition block! This is the reason that doing "more" might be faster.
</div>
</td>
</tr>
</table>
{: .notice--info}

Back to the constraints. There's one thing we can **never** do: move a pivot that was previously partitioned. I (now) call them "buried pivots" (since they're in their final resting place, get it?); Everyone knows, you don't move around dead bodies, that's always the first bad thing that happens in a horror movie. There's our motivation: not being the stupid person who dies first. That's about it. It sounds simple, but it requires some more serious explanation: When a previous partition operation is complete, the pivot used during that operation is moved to its final resting place. It's new position is used to subdivide the array, and effectively stored throughout numerous call stacks of our recursive function. There's a baked-in assumption here that all data left/right of that buried pivot is smaller/larger than it. And that assumption must **never** be broken. If we intend to **re-partition** data to the left and right of a given partition, as part of this overlapping alignment effort, we need to consider that this extra data might already contain buried pivots, and we can not, under any circumstances ever move them again.  
In short: Buried pivots stay buried where we left them, or bad things happen.

When we call our partitioning operation, we have to consider what initially looks like an asymmetry of the left and right edges of our to-be-partitioned segment:

* For the left side:
  * There might not be additional room on the left with extra data to read from.
    * We are too close to the edge of the array on the left side!  
      This happens for all partitions starting at the left-edge of the entire array.
  * We always partition first left, then right of any buried pivot, we know for a fact that all elements left of "our" partition at any given moment are sorted. e.g. they are all buried pivots, and we can't re-order them.
  * *Important:* We also know that each of those values is smaller than or equal to whatever pivot value we *will select* for the current partitioning operation.

* For the right side, it is almost the same set of constraints:
  * There might not be additional room on the right with extra data to read from.
    * We are too close to the edge of the array on the right side!  
      This happens for all partitions ending on the right-edge of the entire array.
  * The immediate value to our right side is a buried pivot, and all other values to its right are larger-than-or-equal to it.
  * There might be additional pivots immediately to our right as well.
  * *Important:* We also know that each of those values is larger-then-or-equal to whatever pivot value we *will select* for the current partitioning operation.

All this information is hard to integrate at first, but what it boils down to is that whenever we load up the left overlapping vector, there are anywhere between 1-7 elements we are **not** allowed to reorder on the *left side*, and when we load the right overlapping vector, there are, again, anywhere between 1-7 elements we are **not** allowed to re-order on *that right side*. That's the challenge; the good news is that all those overlapping elements are also guaranteed to also be smaller/larger than whatever pivot we end up selecting from out original (sans overlap) partition. This knowledge gives us the edge we need: We know in advance that the extra elements will generate predictable comparison results compared to *any* pivot *within* our partition.

What we need are permutation entries that are ***stable***. I'm coining this phrase freely as I'm going along:  
Stable partitioning means that the partitioning operation **must not** *reorder* values that need to go on the left amongst themselves (we keep their internal ordering amongst themselves). Likewise, it **must not** reorder the values that go on the right amongst themselves. If we manage to do this, we're in the clear: The combination of stable permutation and predictable comparison results means that the overlapping elements will stay put while other elements will be partitioned properly on both edges of our overlapping partition. After this weird permutation, we just need to forget we ever read those extra elements, and the whole thing just... works? ... yes!

Let's start with cementing this idea of what stable partitioning is: Up to this point, there was no such requirement, and the initial partition tables I generated failed to satisfy this requirement.
Here's a simple example for stable/unstable permutation entries, let's imagine we partition the following values around a pivot value of 500:

| Bit                        | 0    | 1    | 2    | 3    | 4    | 5    | 6    | 7    |
| -------------------------- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| `Vector256<T>` Value       | 99   | 100  | 666  | 101  | 102  | 777  | 888  | 999  |
| Mask                       | 0    | 0    | 1    | 0    | 0    | 1    | 1    | 1    |
| Unstable Permutation       | 0    | 1    | **7** | 2    | 3    | **6** | **5** | **4** |
| Unstable Result            | 99 | 100 | 101 | 102 | **999** | **888** | **777** | **666** |
| Stable Permutation         | 0    | 1    | 4    | 2    | 3    | 5    | 6    | 7    |
| Stable Result              | 99 | 100 | 101 | 102 | 666 | 777 | 888 | 999 |

In the above example, the unstable permutation is a perfectly *<u>valid</u>* permutation for general case partitioning. It successfully partitions the sample vector around the pivot value of 500, but the 4 elements marked in bold are re-ordered with respect to each other when compared to the original array. In the stable permutation entry, the internal ordering amongst the partitioned groups is *preserved*.

Armed with new, stable permutation entries, We can proceed with this overlapping re-partitioning hack: The idea is to find the optimal alignment point on the left and on the right (assuming one is available, e.g. there is enough room on that side), read that data with the `LoadVectorAligned256` intrinsic, and partition it into the temporary area. The final twist: We need to keep tabs on how many elements *do not belong* to this partition (e.g. originate from our overlap gymnastics), and remember not to copy them back into our partition at the end of the function, relying on our stable partitioning to keep them grouped at the edges of the temporary buffer we're copying from... To my amazement, that was kind of it. It just works! (I've conveniently ignored a small edge-case here in words, but not in the code :).

The end result is super delicate. If you feel you've got it, skip this paragraph, but if you need an alternative view on how this works, here it is: I've just described how to partition the initial 2x8 elements (8 on each side); out of those initial 8, We *always* have a subset that must **never** be reordered (the overlap), and a subset we need to re-order, as is normal, with respect to some pivot. We know that whatever *possible* pivot value *might* be selected from our internal partition, it will always be larger/smaller than the elements in the overlapping areas. Knowing that, we can rely on having stable permutation entries that **do not** reorder those extra elements. In the end, we read extra elements, feed them through our partitioning machine, but ignore the extra overlapping elements and avoid *all* scalar partitioning thanks to this scheme.  

In the end, we literally get to eat our cake and keep it whole: For the 99% case we **kill** scalar partitioning all-together, doing *zero* scalar work, at the same time aligning everything to `Vector256<T>` size and being nice to our processor. Just to make this victory a tiny touch sweeter, even the *initial* 2x8 partially overlapping vectors are read using aligned reads!
I named this approach "overligned" (overlap + align) in my code-base; it is available in full in [`B5_2_DoublePumpOverlined.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/B5_2_DoublePumpOverlined.cs). It implements this overlapping alignment approach, with some extra small points for consideration:

* When it is **impossible** to align outwards, we fall back to the alignment mechanic introduced in the previous section.  
  This is uncommon: Going back to the statistical data we collected about random-data sorting in the 3<sup>rd</sup> post, we anticipate a recursion depth of around 40 when sorting 1M elements and ~340K partitioning calls. We will have *at least* 40x2 (for both sides) such cases where we align inwards for that 1M case, as an example. This is small change compared to the `340K - 80` calls we can optimize with outward alignment, but it does mean we have to keep that old code lying around.
* Once we calculate for a given partition how much alignment is required on each side, we can cache that calculation recursively for the entire depth of the recursive call stack: This again reduces the overhead we are paying for this alignment strategy.
  In the code you'll see I'm squishing two 32-bit integers into a 64-bit value I call `alignHint` and I keep reusing one half of 64-bit value without recalculating the alignment *amount*; If we've made it this far, let's shave a few more cycles off while we're here.

There's another small optimization I tacked on to this version, which I'll discuss immediately after providing the results:

<div markdown="1">
<div class="stickemup">

{% codetabs %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Scaling %}
<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Performance scale: Array.Sort (solid gray) is always 100%, and the other methods are scaled relative to it" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,64K,100K,1M,1.5M,10M
Jedi,         1   , 1  , 1 , 1  , 1   , 1  , 1  , 1
Overlined, 1.012312,    0.995069647, 0.904921232, 0.905092554, 0.915092554, 0.9212314, 0.929801383, 0.960170878

<!-- 
{ 
 "data" : {
  "datasets" : [
  { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3}
  },
  { 
    "backgroundColor": "rgba(33,220,33,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 60, "hachureGap": 3}
  }  
  ]
 },
 "options": {
    "title": { "text": "AVX2 Overlined Sorting - Scaled to Jedi", "display": true },
    "scales": { 
      "yAxes": [{
       "ticks": {
         "fontFamily": "Indie Flower",
         "min": 0.88, 
         "callback": "ticksPercent"
        },
        "scaleLabel": {
          "labelString": "Scaling (%)",
          "display": true
        }
      }]
    },
    "annotation": {
      "annotations": [{
        "drawTime": "afterDatasetsDraw",
        "type": "line",
        "mode": "vertical",
        "scaleID": "x-axis-0",
        "value": "1.5M",

        "borderColor": "#666666",
        "borderWidth": 2,
      "borderDash": [5, 5],
       "borderDashOffset": 5,
        "label": {
          "yAdjust": 5,
          "backgroundColor": "rgba(255, 0, 0, 0.75)",
          "fontFamily": "Indie Flower",
          "fontSize": 14,
          "content": "L3 Cache Size",
          "enabled": true
        }
      },
      {
        "drawTime": "afterDatasetsDraw",
        "type": "line",
        "mode": "vertical",
        "scaleID": "x-axis-0",
        "value": "64K",
        "borderColor": "#666666",
        "borderWidth": 2,
      "borderDash": [5, 5],
       "borderDashOffset": 5,
        "label": {
          "yAdjust": 65,
          "backgroundColor": "rgba(255, 0, 0, 0.75)",
          "fontFamily": "Indie Flower",
          "fontSize": 14,
          "content": "L2 Cache Size",
          "enabled": true
        }
      }]
    }
 },
 "defaultOptions": {{ page.chartjs | jsonify }}
}
--> </canvas>

</div>
</div>
</div>
</div>
</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Time/N %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Time in nanoseconds spent sorting per element. Array.Sort (solid gray) is the baseline, again" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,100K,1M,10M
Jedi, 19.4547,  20.8907,  23.8802, 24.7229, 22.8053, 25.7011
Overlined, 20.092,  20.7878,  21.6097, 22.6238, 21.2044, 24.6774
<!-- 
{ 
 "data" : {
  "datasets" : [
  { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3}
  },
  { 
    "backgroundColor": "rgba(33,220,33,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 60, "hachureGap": 3}
  }
  ]
 },
 "options": {
    "title": { "text": "AVX2 Jedi Sorting + Overlined - log(Time/N)", "display": true },
    "scales": { 
      "yAxes": [{ 
        "type": "logarithmic",
        "ticks": {
          "min": 15,
          "max": 28,
          "callback": "ticksNumStandaard",
          "fontFamily": "Indie Flower"          
        },
        "scaleLabel": {
          "labelString": "Time/N (ns)",
          "fontFamily": "Indie Flower",
          "display": true
        }
      }]
    }
 },
 "defaultOptions": {{ page.chartjs | jsonify }}
}
--> </canvas>

</div>
</div>
</div>
</div>
</div>
{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-list-alt'></i> Benchmarks %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<table class="table datatable"
  data-json="../_posts/Bench.BlogPt5_2_Int32_-report.datatable.json"
  data-id-field="name"
  data-pagination="false"
  data-page-list="[9, 18]"
  data-intro="Each row in this table represents a benchmark result" data-position="left"
  data-show-pagination-switch="false">
  <thead data-intro="The header can be used to sort/filter by clicking" data-position="right">
    <tr>
        <th data-field="TargetMethodColumn.Method" data-sortable="true"
         data-filter-control="select">
          <span
              data-intro="The name of the benchmarked method"
              data-position="top">
            Method<br/>Name
          </span>
        </th>
        <th data-field="N" data-sortable="true"
            data-value-type="int" data-filter-control="select">
            <span
              data-intro="The size of the sorting problem being benchmarked (# of integers)"
              data-position="top">
            Problem<br/>Size
            </span>
        </th>
        <th data-field="TimePerNDataTable" data-sortable="true"
            data-value-type="float2-interval-muted">
            <span
              data-intro="Time in nanoseconds spent sorting each element in the array (with confidence intervals in parenthesis)"
              data-position="top">
              Time /<br/>Element (ns)
            </span>
        </th>
        <th data-field="RatioDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal-percentage">
            <span data-intro="Each result is scaled to its baseline (Array.Sort in this case)"
                  data-position="top">
                  Scaling
            </span>
        </th>
        <th data-field="Measurements" data-sortable="true" data-value-type="inline-bar-vertical">
            <span data-intro="Raw benchmark results visualize how stable the result it. Longest/Shortest runs marked with <span style='color: red'>Red</span>/<span style='color: green'>Green</span>" data-position="top">Measurements</span>
        </th>
    </tr>
  </thead>
</table>
</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-info-sign'></i> Setup %}

```bash
BenchmarkDotNet=v0.12.0, OS=clear-linux-os 32120
Intel Core i7-7700HQ CPU 2.80GHz (Kaby Lake), 1 CPU, 4 logical and 4 physical cores
.NET Core SDK=3.1.100
  [Host]     : .NET Core 3.1.0 (CoreCLR 4.700.19.56402, CoreFX 4.700.19.56404), X64 RyuJIT
  Job-DEARTS : .NET Core 3.1.0 (CoreCLR 4.700.19.56402, CoreFX 4.700.19.56404), X64 RyuJIT

InvocationCount=3  IterationCount=15  LaunchCount=2
UnrollFactor=1  WarmupCount=10

$ grep 'stepping\|model\|microcode' /proc/cpuinfo | head -4
model           : 158
model name      : Intel(R) Core(TM) i7-7700HQ CPU @ 2.80GHz
stepping        : 9
microcode       : 0xb4
```

{% endcodetab %}

{% endcodetabs %}
</div>

This is much better! The improvement is much more pronounced here, and we have a lot to consider:

* The performance improvements are not spread evenly through-out the size of the sorting problem.
* I've conveniently included two vertical markers, per my specific machine model, they show the size of the L2/L3 caches translated to `#` of 32-bit elements in our array.
* It can be clearly seen that as long as we're sorting roughly within the size of our L2-L3 cache size range, this optimization pays in spades: we're seeing ~10% speedup in runtime in many cases!
* It is also clear that as we progress outside the size of the L2 into the L3 cache size, and ultimately exhaust the size of our caches entirely, the returns on this optimization diminish gradually.
* While not shown here, since I've lost access to that machine, on older Intel/AMD machines, where only one load operation can be executed by the processor at any given time (Example: Intel Broadwell processors), this can lead to an improvement of 20% in total runtime; This should make sense: the less load ports the CPU has, the better this split-load reducing technique performs.
* Another thing to consider is that in future variations of this code when I finally get access and ability to use AVX-512, with 64-byte wide registers, the effects of this optimization will be much more pronounced again for a different reason: With vector registers spanning 64-bytes each, split-loading becomes a bigger problem (every single un-aligned read becomes a split-load). Therefore, removing it is even more important.

</div>

As the problem size goes beyond the size of the L2 cache, we are hit with the realities of CPU cache latency numbers. As service to the reader here is a visual representation for the [latency numbers for a Skylake-X CPU](https://www.7-cpu.com/cpu/Skylake_X.html) running at 4.3 Ghz:

<center>
<object style="margin: auto; width: 90%" type="image/svg+xml" data="../assets/images/latency.svg"></object>
</center>

The small number of cycles we tack as the penalty of for split-loading (7 in this diagram) on to the memory operations is very real when we compare it to regular L1/L2 cache latency. But once we compare it to L3 or RAM latency, it becomes abundantly clear why we are seeing diminishing returns for this optimization; the penalty is simply too small to notice at those work points.

Finally, for this optimization, we must never forget our moto of trust no one and nothing. Let's double check what the current state of affairs is as far as `perf` is concerned:

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

Seems like this moved the needle, and then some. We started with `86.68%` of `87,102,613` split-loads in our previous version of vectorized partitioning , and now we have `28.68%` of `12,900,387`. In other words: `(0.2668 * 12900387) / (0.8668 * 87102613)` gives us `4.55%`, or a `95.44%` reduction of split-load events for this version.
Not an entirely unpleasant experience.

#### Sub-optimization- Converting branches to arithmetic: :+1:

By this time, my code contained quite a few branches to deal with various edge cases around alignment, and I pulled another rabbit out of the optimization hat that is worth mentioning: We can convert simple branches into arithmetic operations. Many times, we end up having branches with super simple code behind them; here's a real example I used to have in my code, as part of some early version of overlinement, which we'll try to optimize:

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
int leftAlign;
... // Calculate left align here...
if (leftAlign < 0) {
    readLeft += 8;
}
```

</div>

This looks awfully friendly, and it is unless `leftAlign` and therefore the entire branch is determined by random data we read from the array, making the CPU mispredict this branch too often than we'd care for it to happen. In my case, I had two branches like this, and each of them was happening at a rate of `1/8`. So enough for me to care. The good news is that we can re-write this, entirely in C#, and replace the potential misprediction with a constant, predictable (and often shorter!) data dependency. Let's start by inspecting the re-written "branch":

</div>

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
int leftAlign;
... // Calculate left align here...
// Signed arithmetic FTW
var leftAlignMask = leftAlign >> 31;
// the mask is now either all 1s or all 0s depending on leftAlign's sign!
readLeft += 8 & leftALignMask;
```

</div>

By taking the same value we were comparing to 0 and right shifting it, we are performing an arithmetic right shift. This takes the top bit, which is either `0/1` depending on `leftAlign`'s sign bit, and essentially propagates it throughout the entire 32-bit value, which is then assigned to the `lestAlignMask` variable. We've essentially taken what was previously the result of the comparison as part of the branch (the sign bit), transforming it into a mask. We then proceed to take the mask and use it to control the outcome of the `+= 8` operation, effectively turning it into *either* a `+= 8` -or- a `+= 0` operation, depending on the value of the mask!  
This turns out to be a quite effective way, again, for simple branches only, at converting a potential misprediction event costing us 15 cycles, with a 100% constant 3-4 cycles data-dependency for the CPU: It can be thought as a "signaling" mechanism where we tell the CPU not to speculate on the result of the branch but instead complete the `readLeft +=` statement only after waiting for the right-shift (`>> 31`) and the bitwise and (`&`) operation to propagate through its pipeline.

<table style="margin-bottom: 0em">
<tr>
<td style="border: none; padding-top: 0; padding-bottom: 0; vertical-align: top"><span class="uk-label">Note</span></td>
<td style="border: none; padding-top: 0; padding-bottom: 0"><div markdown="1">
I referred to this as an old geezer's optimization since modern processors already support this internally in the form of a `CMOV` instruction, which is more versatile, faster and takes up less bytes in the instruction stream while having the same "do no speculate on this" effect on the CPU. *The only issue* is we don't have `CMOV` in the CoreCLR JIT (Mono's JIT, peculiarly does support this both with the internal JIT and naturally with LLVM...).  
As a side note to this side note, I'll add that this is such an old-dog trick that LLVM even detects such code and de-optimizes it back into a "normal" branch and then proceeds to optimize it again into `CMOV`, which I think is just a very cool thing, regardless :)
</div>
</td>
</tr>
</table>
{: .notice--info}

</div>

I ended up replacing about 5-6 super simple/small branches this way. I won't show direct performance numbers for this, as this is already part of the overlined version; I can't say it improved performance considerably for my test runs, but it did reduce the jitter of those runs, which can be seen in the reduced error bars and tighter confidence intervals shown in the benchmark results above.

### Coming to terms with bad speculation

At the end of part 3, we came to a hard realization that our code is badly speculating inside the CPU. Even after simplifying the branch code in our loop in part 4, the bad speculation remained there, staring at us persistently. If you recall, we experienced a lot of bad-speculation effects when sorting the data with our vectorized code, and profiling using hardware counters showed us that while `InsertionSort` was the cause of most of the bad-speculation events (41%), our vectorized code was still responsible for 32% of them. Let's try to think about that mean nasty branch, stuck there, in the middle of our beautiful loop:

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
int* nextPtr;
if ((byte *) writeRight - (byte *) readRight < N * sizeof(int)) {
    nextPtr   =  readRight;
    readRight -= N;
} else {
    nextPtr  =  readLeft;
    readLeft += N;
}

PartitionBlock(nextPtr, P, pBase, ref writeLeft, ref writeRight);
```

</div>

Long story short: We ended up sneaking up a data-based branch into our code in the form of this side-selection logic. Whenever we try to pick a side, we would read from next is where we put the CPU in a tough spot. We're asking it to speculate on something it *can't possibly speculate on successfully*. Our question is: "Oh CPU, CPU in the socket, Which side is closer to being over-written of them all?", to which the answer is completely data-driven. In other words, it depends on how the last round(s) of partitioning mutated the pointers involved in the comparison. It might sound like an easy thing for the CPU to check, but we have to remember it is attempting to execute ~100 or so instructions into the future, as it is required to speculate on the result: the previous rounds of partitioning have not yet been fully-executed, internally. The CPU guesses, at best, based on stale data, and we know, as the grand designers of this mess, that its best guess is no better here than flipping a coin. Quite sad. You have to admit it is ironic we managed to do this whole big circle around our own tails just to come-back to having a branch misprediction based on the random array data. Mis-predicting here seems unavoidable. Or is it?

#### Replacing the branch with arithmetic: :-1:

Could we replace this branch with arithmetic, just like we've done a couple of paragraphs above? Yes we can.
Consider this alternative version:
</div>

```csharp
var readRightMask =
    (((byte*) writeRight - (byte*) readRight - N*sizeof(int))) >> 63;
var readLeftMask =  ~readRightMask;
// If readRightMask is 0, we pick the left side
// If readLeftMask is 0, we pick the right side
var readRightMaybe  = (ulong) readRight & (ulong) readRightMask;
var readLeftMaybe   = (ulong) readLeft  & (ulong) readLeftMask;

PartitionBlock((int *) (readLeftMaybe + readRightMaybe),
               P, pBase, ref writeLeft, ref writeRight);

var postFixUp = -32 & readRightMask;
readRight = (int *) ((byte *) readRight + postFixUp);
readLeft  = (int *) ((byte *) readLeft  + postFixUp + 32);
```

What the code above does, except for causing a nauseating headache, is taking the same concept of turning branches into arithmetic from the previous section and using it to get rid of that nasty branch: We take the comparison and turn it into a negative/positive number, then proceed to use it to generate masks we use to execute the code that used to reside under the branch.

I don't want to dig deep into this. While its technically sound, and does what we need it to do, it's more important to focus on how this performs:

<div markdown="1">
<div class="stickemup">

{% codetabs %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Scaling %}
<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Performance scale: Array.Sort (solid gray) is always 100%, and the other methods are scaled relative to it" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,100K,1M,10M
Overlined,         1   , 1   , 1  , 1   , 1    , 1
Branchless, 0.87253937, 0.951842168, 1.104715689, 1.140662148, 1.253573179, 1.379499062

<!-- 
{ 
 "data" : {
  "datasets" : [
  { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3}
  },
  { 
    "backgroundColor": "rgba(33,220,33,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 60, "hachureGap": 3}
  }  
  ]
 },
 "options": {
    "title": { "text": "AVX2 Branchless Sorting - Scaled to Overlined", "display": true },
    "scales": { 
      "yAxes": [{
       "ticks": {
         "fontFamily": "Indie Flower",
         "min": 0.80, 
         "callback": "ticksPercent"
        },
        "scaleLabel": {
          "labelString": "Scaling (%)",
          "display": true
        }
      }]
    }
 },
 "defaultOptions": {{ page.chartjs | jsonify }}
}
--> </canvas>

</div>
</div>
</div>
</div>
</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Time/N %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Time in nanoseconds spent sorting per element. Array.Sort (solid gray) is the baseline, again" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,100K,1M,10M
Overlined, 20.3199,21.0354,21.6787,23.0622,23.246,24.7603
Branchless, 17.7252,20.0221,23.9488,26.3062,29.1405,34.1567

<!-- 
{ 
 "data" : {
  "datasets" : [
  { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3}
  },
  { 
    "backgroundColor": "rgba(33,220,33,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 60, "hachureGap": 3}
  }
  ]
 },
 "options": {
    "title": { "text": "AVX2 Jedi Sorting + Aligned - log(Time/N)", "display": true },
    "scales": { 
      "yAxes": [{ 
        "type": "logarithmic",
        "ticks": {
          "min": 15,
          "max": 35,
          "callback": "ticksNumStandaard",
          "fontFamily": "Indie Flower"          
        },
        "scaleLabel": {
          "labelString": "Time/N (ns)",
          "fontFamily": "Indie Flower",
          "display": true
        }
      }]
    }
 },
 "defaultOptions": {{ page.chartjs | jsonify }}
}
--> </canvas>

</div>
</div>
</div>
</div>
</div>
{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-list-alt'></i> Benchmarks %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<table class="table datatable"
  data-json="../_posts/Bench.BlogPt5_3_Int32_-report.datatable.json"
  data-id-field="name"
  data-pagination="false"
  data-page-list="[9, 18]"
  data-intro="Each row in this table represents a benchmark result" data-position="left"
  data-show-pagination-switch="false">
  <thead data-intro="The header can be used to sort/filter by clicking" data-position="right">
    <tr>
        <th data-field="TargetMethodColumn.Method" data-sortable="true"
         data-filter-control="select">
          <span
              data-intro="The name of the benchmarked method"
              data-position="top">
            Method<br/>Name
          </span>
        </th>
        <th data-field="N" data-sortable="true"
            data-value-type="int" data-filter-control="select">
            <span
              data-intro="The size of the sorting problem being benchmarked (# of integers)"
              data-position="top">
            Problem<br/>Size
            </span>
        </th>
        <th data-field="TimePerNDataTable" data-sortable="true"
            data-value-type="float2-interval-muted">
            <span
              data-intro="Time in nanoseconds spent sorting each element in the array (with confidence intervals in parenthesis)"
              data-position="top">
              Time /<br/>Element (ns)
            </span>
        </th>
        <th data-field="RatioDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal-percentage">
            <span data-intro="Each result is scaled to its baseline (Array.Sort in this case)"
                  data-position="top">
                  Scaling
            </span>
        </th>
        <th data-field="Measurements" data-sortable="true" data-value-type="inline-bar-vertical">
            <span data-intro="Raw benchmark results visualize how stable the result it. Longest/Shortest runs marked with <span style='color: red'>Red</span>/<span style='color: green'>Green</span>" data-position="top">Measurements</span>
        </th>
    </tr>
  </thead>
</table>
</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-info-sign'></i> Setup %}

```bash
BenchmarkDotNet=v0.12.0, OS=clear-linux-os 32120
Intel Core i7-7700HQ CPU 2.80GHz (Kaby Lake), 1 CPU, 4 logical and 4 physical cores
.NET Core SDK=3.1.100
  [Host]     : .NET Core 3.1.0 (CoreCLR 4.700.19.56402, CoreFX 4.700.19.56404), X64 RyuJIT
  Job-DEARTS : .NET Core 3.1.0 (CoreCLR 4.700.19.56402, CoreFX 4.700.19.56404), X64 RyuJIT

InvocationCount=3  IterationCount=15  LaunchCount=2
UnrollFactor=1  WarmupCount=10

$ grep 'stepping\|model\|microcode' /proc/cpuinfo | head -4
model           : 158
model name      : Intel(R) Core(TM) i7-7700HQ CPU @ 2.80GHz
stepping        : 9
microcode       : 0xb4
```

{% endcodetab %}
{% endcodetabs %}
</div>

Look, I'm not here to sugar-coat it: This looks like an unmitigated disaster. But I claim that it is one we can learn a lot from in the future.
With the exception of sorting `<= 100` elements, as the problem grows, the situation is getting much worse.

To double-check that everything is sound, I ran `perf` recording the `instructions`, `branches` and `branch-misses` events for both versions for sorting `100,000` elements.

The command line used was this:

```bash
$ perf record -F max -e instructions,branches,branch-misses \
    ./Example --type-list DoublePumpOverlined \
              --size-list 100000 --max-loops 1000 --no-check
$ perf record -F max -e instructions,branches,branch-misses \
    ./Example --type-list DoublePumpBranchless \
              --size-list 100000 --max-loops 1000 --no-check
```

If you're one of those sick people who likes to look into other people's sorrows, here is a [gist with the full results](https://gist.github.com/damageboy/79368e350364348c6ca476492a63f052), if you're more normal, and to keep things simple, I've processed the results and presenting them here in table form:

<center>
<object style="margin: auto; width: 90%" type="image/svg+xml" data="../assets/images/overlined-branchless-counters.svg"></object>
</center>

</div>

This is pretty amazing if you think about it:

* The number of branches was cut in half: This makes sense, the loop control itself is a branch instuction after all, so it remains even in the `Branchless` variant.
* The branches that remain in the `branchless` version are all easy to predict, and we see that the `branch-misses` counter shows us those are down to nothing.  
  This means that there is no mistake: We succeeded in a targeted assassination of that branch; however, there was a lot of collateral damage...
* The verbiage of the branchless code, expressed in the `instructions` counter is definitely costing us something here:  
  The number of executed instructions inside our partition loop have gone up by 17%, which is a lot.
  
The slowdown we've measured here is directly related to NOT having `CMOV` available to us through the CoreCLR JIT. but I really don't think that this is the entire story here. It's hard to express this in words, but
the slope at which the branchless code is slowing down compared to the previous version is very suspicious in my eyes.  
There is an expression we use in Hebrew a lot for this sort of situation: "The operation was successful, but the patient died". There is no question that this is one of those moments.
This failure to accelerate the sorting operation, and specifically the way it fails, increasingly as the problem size grows, is very telling in my eyes.
I have an idea of why this is and how we might be able to go around it. But, for today, our time is up. I'll try and get back to this much much later in this series,
and hopefully, we'll all be wiser for it.

---
[^0]: Remember that the CPU knows nothing about two different cache-lines. They might actually be on a page boundary as well, which means they might be in two different DRAM chips, or perhaps, a single split-line access causes our poor CPU to communicate with a different socket, where another memory controller is responsible to reading the memory from its own DRAM modules!
[^1]: Most modern Intel CPUs can actually address the L1 cache units twice per cycle, at least when it comes to reading data, by virtue of having two load-ports. That means they can actually request two cache-line as the same time! But this still causes more load on the cache and bus. In our case, we must also remember we will be reading an additional cache-line for our permutation entry...
[^2]: This specific AVX2 intrinsic will actually fail if/when used on non-aligned addresses. But it is important to note that it seems it won’t actually run faster than the previous load intrinsic we’ve used: `AVX2.LoadDquVector256` as long as the actual addresses we pass to both instructions are 32-byte aligned. In other words, it’s very useful for debugging alignment issues, but not that critical to actually call that intrinsic!
[^3]: I could be wrong about that last statement, but I couldn't find anything quite like this discussed anywhere, and believe me, I've searched. If anyone can point me out to someone doing this before, I'd really love to hear about it; there might be more good stuff to read about there...
