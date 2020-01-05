---
title: "This Goes to Eleven (Pt. 1/6)"
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
date: 2019-08-18 08:26:28 +0300
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

#categories: coreclr intrinsics vectorization quicksort sorting	
---


# Let’s do this

Let's get in the ring and show what AVX/AVX2 intrinsics can really do for a non-trivial problem, and even discuss potential improvements that future CoreCLR versions could bring to the table.

Everyone needs to sort arrays, once in a while. We think of it as a solved problem and that nothing can be further done about it in 2019, except for waiting for newer, marginally faster machines to pop-up[^0]. However, that is not the case, and if you join me in this rather long journey, we’ll end up with a replacement function for `Array.Sort`, written in C# that can outperform CoreCLR's own C++[^3] based code by a factor north of 10x.  
Sounds interesting? If so, down the rabbit hole we go…

<span class="uk-label">Note</span> that in the final days before posting this series, Intel started seeding a CPU microcode update that is/was affecting the performance of the released version of CoreCLR 3.0/3.1 quite considerably. I managed to stir up a [small commotion](https://twitter.com/damageboy/status/1194751035136450560) as this was unraveling in my benchmarks. As it happened, my code was (not coincidentally) less affected by this change, while CoreCLRs `Array.Sort()` took a 20% nosedive. Let it never be said I’m nothing less than chivalrous, for I rolled back the microcode update and for this **entire** series I’m going to run against a much faster version of `Array.Sort()` than what you, the reader, are probably using, Assuming you update your machine from time to time. For the technically inclined, here’s a whole footnote[^4] on how to double check what your machine is actually running. I also opened two issues in the CoreCLR repo about attempting to mitigate this both in CoreCLRs C++ code and separately in the JIT. If/when there is movement on those fronts, the microcode you’re running will become less of an issue to begin with, but for now, this just adds another level of unwarranted complexity to our lives.
{: .notice--warning}

A while back now, I was reading the post by Stephen Toub about [Improvements in CoreCLR 3.0](https://devblogs.microsoft.com/dotnet/performance-improvements-in-net-core-3-0/), and it became apparent that hardware intrinsics were common to many of these, and that so many parts of CoreCLR could still be sped up with these techniques, that one thing led to another, and I decided an attempt to apply hardware intrinsics to a larger problem than I had previously done myself was in order. To see if I could rise to the challenge, I decided to take on array sorting and see how far I can go.

What I came up with eventually would become a re-write of `QuickSort`/`Array.Sort()` with AVX2 hardware intrinsics. Fortunately, choosing  sorting, and specifically, `QuickSort`, makes for a great blog post series, since:

* Everyone should be familiar with the domain and even the original (sorting is the bread and butter of learning computer science, really, and QuickSort is the queen of all sorting algorithms).
* It's relatively easy to explain/refresh on the original.
* If I can make it there, I can make it anywhere.
* I had no idea how to do it.

I started with searching various keywords and found an interesting paper titled: [Fast Quicksort Implementation Using AVX Instructions](http://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.1009.7773&rep=rep1&type=pdf) by Shay Gueron and Vlad Krasnov. That title alone made me think this is about to be a walk in the park. While initially promising, it wasn’t good enough as a drop-in replacement for `Array.Sort` for reasons I’ll shortly go into. I ended up (or rather, still am, to be honest) having a lot of fun expanding on their idea. I will submit a proper pull-request to start a discussion with CoreCLR devs about integrating this code into the main [dotnet/runtime](https://github.com/dotnet/runtime) repository, but for now, let's talk about sorting.

Since there’s a lot to go over here, I’ve split it up into no less than 6 parts:

1. In this part, we start with a refresher on `QuickSort` and how it compares to `Array.Sort()`. If you don’t need a refresher, skip it and get right down to part 2 and onwards. I recommend skimming through, mostly because I’ve got excellent visualizations which should be in the back of everyone’s mind as we deal with vectorization & optimization later.
2. In [part 2]({% post_url 2019-08-19-this-goes-to-eleven-pt2 %}), we go over the basics of vectorized hardware intrinsics, vector types, and go over a handful of vectorized instructions we’ll use in part 3. We still won't be sorting anything.
3. In [part 3]({% post_url 2019-08-20-this-goes-to-eleven-pt3 %}), we go through the initial code for the vectorized sorting, and we’ll start seeing some payoff. We finish agonizing courtesy of the CPU’s Branch Predictor, throwing a wrench into our attempts.
4. In [part 4]({% post_url 2019-08-21-this-goes-to-eleven-pt4 %}), we go over a handful of optimization approaches that I attempted trying to get the vectorized partitioning to run faster. We'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of all the remaining scalar code- by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization.
6. Finally, in part 6, I’ll list the outstanding stuff/ideas I have for getting more juice and functionality out of my vectorized code.

## QuickSort Crash Course

`QuickSort` is deceivingly simple.  
No, it really is.  
In 20 lines of C# or whatever language you can sort numbers. Lots of them, and incredibly fast. However, try and change something about it; nudge it in the wrong way, and it will quickly turn around and teach you a lesson in humility. It is hard to improve on it without breaking any of the tenants it is built upon.

### In words

Before we discuss any of that, let’s describe `QuickSort` in words, code, pictures, and statistics:

* It uses a *divide-and-conquer* approach.
  * In other words, it's recursive.
  * It has an average of $$\mathcal{O}(n\log{}n)$$ comparisons for *n* items.
* It performs an in-place sort.

That last point, referring to in-place sorting, sounds simple and neat, and it sure is from the perspective of the user: no additional memory allocation needs to occur regardless of how much data they're sorting. While that's great, I’ve spent days trying to overcome the correctness and performance challenges that arise from it, specifically in the context of vectorization. It is also essential to remain in-place since I intend for this to become a *drop-in* replacement for `Array.Sort`.

More concretely, `QuickSort` works like this:

1. Pick a pivot value.
2. **Partition** the array around the pivot value.
3. Recurse on the left side of the pivot.
4. Recurse on the right side of the pivot.

Picking a pivot could be a mini-post in itself, but again, in the context of competing with `Array.Sort` we don’t need to dive into it, we’ll copy whatever CoreCLR does, and get on with our lives.  
CoreCLR uses a pretty standard scheme of median-of-three for pivot selection, which can be summed up as: “Let’s sort these 3 elements: In the first, middle and last positions, then pick the middle one of those three as the pivot”.

**Partitioning** the array is where we spend most of the execution time: we take our selected pivot value and rearrange the array segment that was handed to us such that all numbers *smaller-than* the pivot are in the beginning or **left**, in no particular order amongst themselves. Then comes the *pivot*, in its **final** resting position, and following it are all elements *greater-than* the pivot, again in no particular order amongst themselves.

After partitioning is complete, we recurse to the left and right of the pivot, as previously described.

That’s all there is: this gets millions, billions of numbers sorted, in-place, efficiently as we know how to do 60+ years after its invention.

Bonus trivia points for those who are still here with me: [Tony Hoare](https://en.wikipedia.org/wiki/Tony_Hoare), who invented QuickSort back in the early 60s also took responsibility for inventing the `null` pointer concept. So I guess there really is no good without evil in this world.
{: .notice--info}

### In code

```csharp
void QuickSort(int[] items) => QuickSort(items, 0, items.Length - 1);

void QuickSort(int[] items, int left, int right)
{
    if (left == right) return;
    int pivot = PickPivot(items, left, right);
    int pivotPos = Partition(items, pivot, left, right);
    QuickSort(items, left, pivotPos);
    QuickSort(items, pivotPos + 1, right);
}

int PickPivot(int[] items, int left, int right)
{
    var mid = left + ((right - left) / 2);
    SwapIfGreater(ref items[left],  ref items[mid]);
    SwapIfGreater(ref items[left],  ref items[right]);
    SwapIfGreater(ref items[mid],   ref items[right]);
    var pivot = items[mid];
}

int Partition(int[] array, int pivot, int left, int right)
{
    while (left <= right) {
        while (array[left]  < pivot) left++;
        while (array[right] > pivot) right--;

        if (left <= right) {
            var t = array[left];
            array[left++]  = array[right];
            array[right--] = t;
        }
    }
    return left;
}
```

I did say it is deceptively simple, and grasping how QuickSort really works sometimes feels like trying to lift sand through your fingers; To that end I’ve included two more visualizations of QuickSort, which are derivatives of the amazing work done by [Michael Bostock (@mbostock)](https://observablehq.com/@mbostock) with [d3.js](https://d3js.org/).

### Visualizing QuickSort’s recursion

One thing that we have to keep in mind is that the same data is partitioned over-and-over again, many times, with ever-shrinking partition sizes until we end up having a partition size of 2 or 3, in which case we can trivially sort the partition as-is and return.

To help see this better, we’ll use this way of visualizing arrays and their intermediate states in QuickSort:

<div markdown="1">
<div markdown="1" class="stickemup">

![QuickSort Legend](../talks/intrinsics-sorting-2019/quicksort-mbostock/quicksort-vis-legend.svg)

</div>

Here, we see an unsorted array of 200 elements (in the process of getting sorted).  
The different sticks represent numbers in the  [-45°..+45°] range, and the angle of each individual stick represents its value, as I hope it is easy to discern.  
We represent the pivots with **two** colors:

* <span style="color: red">**Red**</span> for the currently selected pivot at a given recursion level.
* <span style="color: green">**Green**</span> for previous pivots that have already been partitioned around in previous rounds/levels of the recursion.

Our ultimate goal is to go from the image above to the image below:

</div>

![QuickSort Sorted](../talks/intrinsics-sorting-2019/quicksort-mbostock/quicksort-vis-sorted.svg)

What follows is a static (e.g., non-animated) visualization that shows how pivots are randomly selected at each level of recursion and how, by the next step, the unsorted segments around them become partitioned until we finally have a completely sorted array. Here is how the whole thing looks:

These visuals are auto-generated in Javascript + d3.js, so feel free to hit that "Reload" button and/or change the number of elements in the array  if you feel you want to see a new set of random sticks sorted.
{: .notice--info}

<iframe src="../talks/intrinsics-sorting-2019/quicksort-mbostock/qs-static-reload.html" scrolling="no" style="width:1600px; max-width: 100%;background: transparent;" allowfullscreen=""></iframe>
I encourage you to look at this and try to explain to yourself what QuickSort "does" here, at every level. What you can witness here is the interaction between pivot selection, where it "lands" in the next recursion level (or row), and future pivots to its left and right and in the next levels of recursion. We also see how, with every level of recursion, the partition sizes decrease in until finally, every element is a pivot, which means sorting is complete.

### Visualizing QuickSort’s Comparisons/Swaps

While the above visualization really does a lot to help understand **how** `QuickSort` works, I also wanted to leave you with an impression of the total amount of work done by `QuickSort`:

<div markdown="1">
<div class="stickemup">
<iframe src="../talks/intrinsics-sorting-2019/quicksort-mbostock/qs-animated-playpause.html" scrolling="no" style="width:1600px; height: 250px; max-width: 100%;background: transparent;" allowfullscreen=""></iframe>
</div>

Above is an **animation** of the whole process as it goes over the same array, slowly and recursively going from an unsorted mess to a completely sorted array.

We can witness just how many comparisons and swap operations need to happen for a 200 element `QuickSort` to complete successfully. There’s genuinely a lot of work that needs to happen per element (when considering how we re-partition virtually all elements again and again) for the whole thing to finish.
</div>

### Array.Sort vs. QuickSort

It's important to note that `Array.Sort` uses a couple of more tricks to get better performance. I would be irresponsible if I didn't mention those since in the later posts, I borrow at least one idea from its play-book, and improve upon it with intrinsics.

`Array.Sort` isn't strictly `QuickSort`; it is a variation on it called [Introspective Sort](https://en.wikipedia.org/wiki/Introsort) invented by [David Musser](https://en.wikipedia.org/wiki/David_Musser) in 1997. What it roughly does is combine QuickSort, HeapSort, and Insertion Sort, and switch dynamically between them according to the recursion depth and the partition size. This last trick, where it switches to using "Insertion Sort" on small partitions, is critical both for the general case and also for intrinsics/vectorization. It is a sound optimization because it replaces (up to) the last 4 levels or so of recursion (for partition sizes `<= 16`) with a single call to an insertion sort. This reduces the overhead associated with recursion with simpler loop-based code, which ultimately runs faster for such small partitions.

As mentioned, I ended up borrowing this idea for my code as the issues around smaller partition sizes are exacerbated when using vectorized intrinsics in the following posts.

## Comparing Scalar Variants

With all this new information, this is a good time to measure how a couple of different scalar (e.g. non-vectorized) versions compare to `Array.Sort`. I’ll show some results generated using [BenchmarkDotNet](https://benchmarkdotnet.org/) (BDN) with:

* `Array.Sort()` as the baseline.
* [`Managed`]((https://github.com/damageboy/VxSort/blob/master/VxSortResearch/Unstable/Scalar/Managed.cs)) as the code I’ve just presented above.
  * This version is just basic QuickSort using regular/safe C#. With this version, every time we access an array element, the JIT inserts bounds-checking machine code around our actual access that ensures the CPU does not read/write outside the memory region owned by the array.
* [`Unmanaged`](https://github.com/damageboy/VxSort/blob/master/VxSortResearch/Unstable/Scalar/Unmanaged.cs) as an alternative/faster version to `Scalar` where:
  * The code uses native pointers and unsafe semantics (using C#‘s new `unmanaged` constraint, neat!).
  * We switch to `InsertionSort` (again, copy-pasted from CoreCLR) when below 16 elements, just like `Array.Sort` does.

I've prepared this last version to show that with unsafe code + `InsertionSort`, we can remove most of the performance gap between C# and C++ for this type of code, which mainly stems from bounds-checking, that the JIT cannot elide for these sort of random-access patterns as well as the jump-to `InsertionSort` optimization.

<span class="uk-label">Note</span> that for this series, We'll benchmark each sorting method with various array sizes (BDN parameter: `N`): $$ 10^i_{i=1\cdots7} $$. I've added a custom column to the BDN column to the report: `Time / N`. This represents the time spent sorting *per element* in the array, and as such, very useful to compare the results on a more uniform scale.
{: .notice--info}

Here are the results in the form of charts and tables. I've included a handy large button you can press to get a quick tour of what each tab contains, what we have here is:

1. A chart scaling the performance of various implementations being compared to `Array.Sort` as a ratio.
2. A chart showing time spent sorting a single element in an array of N elements (Time / N).
3. BDN results in a friendly table form.
4. Statistics/Counters that teach us about what is actually going on under the hood.

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
ArraySort,1,1,1,1,1,1
Scalar,2.04,1.57,1.33,1.12,1.09,1.11
Unmanaged,1.75,1.01,0.99,0.97,0.93,0.95
<!-- 
{ 
 "data" : {
  "datasets" : [
    { 
      "backgroundColor": "rgba(66,66,66,0.35)",
      "rough": { "fillStyle": "solid", "hachureAngle": -30, "hachureGap": 7	}
    },
    { 
      "backgroundColor": "rgba(220,33,33,.6)", 
      "rough": { "fillStyle": "hachure", "hachureAngle": 15, "hachureGap": 6	} 
    },
    { 
      "backgroundColor": "rgba(33,33,220,.9)",
      "rough": { "fillStyle": "hachure", "hachureAngle": -45, "hachureGap": 6	} 
    }]
 },
 "options": {
    "title": { "text": "Scalar Sorting - Scaled to Array.Sort", "display": true },
    "scales": { 
      "yAxes": [{ 
        "ticks": { 
          "min": 0.8, 
          "fontFamily": "Indie Flower",
          "callback": "ticksPercent" 
        },
        "scaleLabel": {
          "labelString": "Scaling (%)",
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

{% codetab <i class='glyphicon glyphicon-stats'></i> Time/N %}
<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Time in nanoseconds spent sorting per element. Array.Sort (solid gray) is the baseline, again" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<div class="benchmark-chart-container">
<canvas data-chart="line">
N,100,1K,10K,100K,1M,10M
ArraySort,12.1123,30.5461,54.641,60.4874,70.7539,80.8431
Scalar,24.7385,47.8796,72.7528,67.7419,77.3906,89.7593
Unmanaged,21.0955,30.9692,54.3112,58.9577,65.7222,76.8631
<!-- 
{ 
 "data" : {
  "datasets" : [
    { "backgroundColor":"rgba(66,66,66,0.35)", "rough": { "fillStyle": "solid", "hachureGap": 6	} },
    { "backgroundColor":"rgba(33,220,33,.6)", "rough": { "fillStyle": "hachure", "hachureAngle": 15, "hachureGap": 6	} },
    { "backgroundColor":"rgba(33,33,220,.9)", "rough": { "fillStyle": "hachure", "hachureAngle": -45, "hachureGap": 6	} }
   ]
 },
 "options": {
    "title": { "text": "Scalar Sorting - log(Time/N)", "display": true },
    "scales": { 
      "yAxes": [{ 
        "type": "logarithmic",
        "ticks": {
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
  data-json="../_posts/Bench.BlogPt1_Int32_-report.datatable.json"
  data-id-field="name"
  data-pagination="false"
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

{% codetab <i class='glyphicon glyphicon-list-alt'></i> Statistics %}
<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<table class="table datatable"
  data-json="../_posts/scalar-vs-unmanaged-stats.json"
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
            <span
              data-intro="The size of the sorting problem being benchmarked (# of integers)"
              data-position="top">Problem<br/>Size</span>
        </th>
        <th data-field="MaxDepthScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <span
              data-intro="The maximal depth of recursion reached while sorting"
              data-position="top">Max<br/>Depth</span>
        </th>
        <th data-field="NumPartitionOperationsScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <span
              data-intro="# of partitioning operations for each sort"
              data-position="top">#<br/>Part-<br/>itions</span>
        </th>
        <th data-field="AverageSmallSortSizeScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <span
              data-intro="For hybrid sorting, the average size that each small sort operation was called with (e.g. InsertionSort)"
              data-position="top">
            Avg.<br/>Small<br/>Sorts<br/>Size
            </span>
        </th>
        <th data-field="NumScalarComparesScaledDataTable" data-sortable="true"
            data-value-type="inline-bar-horizontal">
            <span
              data-intro="How many branches were executed in each sort operation that were based on the unsorted array elements"
              data-position="top">
            # Data-<br/>Based<br/>Branches
            </span>
            </th>
        <th data-field="PercentSmallSortCompares" data-sortable="true"
            data-value-type="float2-percentage">
            <span
              data-intro="What percent of</br>⬅<br/>branches happenned as part of small-sorts"
              data-position="top">
            % Small<br/>Sort<br/>Data-<br/>Based<br/>Branches
            </span>
        </th>
    </tr>
  </thead>
</table>
</div>
{% endcodetab %}

{% endcodetabs %}
</div>

Surprisingly[^1], the unmanaged C# version is running slightly faster than `Array.Sort`, but with one caveat: it only outperforms the C++ version for large inputs. Otherwise, everything is as expected: The purely `Managed` variant is just slow, and the `Unamanged` one mostly is on par with `Array.Sort`.  
These C# implementations were written to **verify** that we can get to `Array.Sort` *like* performance in C#, and they do just that. Running 5% faster for *some* input sizes will not cut it for me; I want it *much* faster. An equally important reason for re-implementing these basic versions is that we can now sprinkle *statistics-collecting-code* magic fairy dust[^2] on them so that we have even more numbers to dig into in the "Statistics" tab: These counters will assist us in deciphering and comparing future results and implementations. In this post they serve us by establishing a baseline. We can see, per each `N` value (with some commentary):

* The maximal recursion depth. Note that:
  * The unmanaged version, like CoreCLR's `Array.Sort` switches to `InsertionSort` for the last couple of recursion levels, therefore, its maximal depth is smaller.
* The total number of partitioning operations performed.
  * Same as above, less recursion ⮚ less partitioning calls.
* The average size of what I colloquially refer to as "small-sort" operations performed (e.g., `InsertionSort` for the `Unmanaged` variant).
  * The `Managed` version doesn't have any of this, so it's just 0.
  * In the `Unmanaged` version, we see a consistent value of 9.x: Given that we special case 1,2,3 in the code and 16 is the upper limit, 9.x seems like a reasonable outcome here.
* The number of branch operations that were user-data dependent; This one may be hard to relate to at first, but it becomes apparent why this is a crucial number to track starting with the 3<sup>rd</sup> post onwards. For now, a definition: This statistic counts *how many* times our code did an `if` or a `while` or any other branch operation *whose condition depended on unsorted user supplied data*!
  * The numbers boggle the mind, this is the first time we get to show how much work is involved.
  * What's even more surprising that for the `Unmanged` variant, the number is even higher (well only surprising if you don't know anything about how `InsertionSort` works...) and yet this version seems to run faster... I have an entire post dedicated just to this part of the problem in this series, so let's just make note of this for now, but already we see peculiar things.
* Finally, I've also included a statistic here that shows what percent of those data-based branches came from small-sort operations. Again, this was 0% for the `Managed` variant, but we can see that a large part of those compares are now coming from those last few levels of recursion that were converted to `InsertionSort`...

Some of these statistics will remain pretty much the same for the rest of this series, regardless of what we do next in future versions, while others radically change; We'll observe and make use of these as key inputs in helping us to figure out how/why something worked, or not!

</div>
## All Warmed Up?

We've spent quite some time polishing our foundations concerning `QuickSort` and `Array.Sort`. I know lengthy introductions are somewhat dull, but I think time spent on this post will pay off with dividend when we next encounter our actual implementation in the 3<sup>rd</sup> post and later on. This might be also a time to confess that just doing the leg-work to provide this refresher helped me come up with at least one, super non-trivial optimization, which I think I’ll keep the lid on all the way until the 6<sup>th</sup> and final post. So never underestimate the importance of "just" covering the basics.

Before we write vectorized code, we need to pick up some knowhow specific to vectorized intrinsics and introduce a few select intrinsics we’ll be using, so, this is an excellent time to break off this post, grab a fresh cup of coffee and head to the [next post]({% post_url 2019-08-19-this-goes-to-eleven-pt2 %}).

---------
[^0]: Which is increasingly taking [more and more](https://github.com/damageboy/analyze-spec-benchmarks#integer) time to happen, due to Dennard scaling and the slow-down of Moore's law...
[^1]: Believe it or not, I pretty much wrote every other version features in this series *before* I wrote the `Unmanaged` one, so I really was quite surprised that it ended up being slightly faster that `Array.Sort`
[^2]: I have a special build configuration called `Stats` which compiles in a bunch of calls into various conditionally compiled functions that bump various counters, and finally, dump it all to json and it eventually makes it all the way into these posts (if you dig deep you can get the actual json files :)
[^3]: Since CoreCLR 3.0 was release, a [PR](https://github.com/dotnet/coreclr/pull/27700) to provide a span based version of this has been recently merged into the 5.0 master branch, but I'll ignore this for the time being as it doesn't seem to matter in this context.
[^4]: You can grab your microcode signature in one of the following methods: On Windows, the easiest way is to install and run the excellent HWiNFO64 application, it will show you the microcode signature. On line a `grep -i microcode /proc/cpuinfo` does the tricks, and macOs: `sysctl -a | grep -i microcode` will get the job done. Unfortunately you’ll have to consult your specific CPU model to figure out the before/after signature, and I can’t help you there, except to point out that the microcode update in question came out in November 13<sup>th</sup> and is about mitigating the JCC errata.
