---
title: "This Goes to Eleven (Pt. 4/6)"
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
date: 2020-01-31 08:26:28 +0300
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

### Dealing with small JIT hiccups: :+1:

One of the more surprising things I've discovered during the optimization journey was that the JIT could generate much better code, specifically around/with pointer arithmetic. With the basic version we got working by the end of the [3<sup>rd</sup> post]({% post_url 2020-01-30-this-goes-to-eleven-pt3 %}), I started turning my attention to the body of the main loop. This is where I presume we spend most of our execution time. I immediately encountered some red-flag raising assembly code, specifically with this single line of code, which we've briefly discussed before:

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
mov     rax,rdx       ; ✓  copy readLeft
sub     rax,r12       ; ✓  subtract writeLeft
mov     rcx,rax       ; ✘  wat?
sar     rcx,3Fh       ; ✘  wat?1?
and     rcx,3         ; ✘  wat?!?!?
add     rax,rcx       ; ✘  wat!?!@#
sar     rax,2         ; ✘  wat#$@#$@
mov     rcx,[rbp-58h] ; ✓✘ copy writeRight, but from stack?
mov     r8,rcx        ; ✓✘ in the loop body?!?!?, Oh lordy!
sub     r8,rsi        ; ✓  subtract readRight
mov     r10,r8        ; ✘  wat?
sar     r10,3Fh       ; ✘  wat?!?
and     r10,3         ; ✘  wat!?!@#
add     r8,r10        ; ✘  wat#$@#$@
sar     r8,2          ; ✘  wat^!#$!#$
cmp     rax,r8        ; ✓  finally, comapre!
```

It's not every day that we get to see two JIT issues with one line of code, I know some people might take this as a bad sign, but in my mind this is great! To me this feels like digging for oil in Texas in the early 20s...
We've practically hit the ground with a pickaxe accidentaly, only to see black liquid seeping out almost immediately!

#### JIT Bug 1: `writeRight` not being optimized into register

One super weird thing that we can see happening here is on <span class="uk-label">L8-9</span> especially when compared to <span class="uk-label">L1</span>. The code merely tries to substract two pairs of pointers, but the generated machine code is weird: 3 out of 4 pointers were correctly lifted out of the stack into registers outside the body of the loop (`readLeft`, `writeLeft`, `readRight`), but the 4<sup>th</sup> one, `writeRight`, is the designated black-sheep of the family and is being continuously read from the stack (and later in that loop body is also written back to the stack, to make things worse).  
There is no good reason for this, and it's super weird that this is happening! What do we do?

For one thing, I've opened up an issue about this weirdness. The issue itself shows just how finicky the JIT is regarding this one variable, and (un)surprisingly, by fudging around the setup code this can be easily worked around for now.  
Here's the original setup code I presented in the previous post, just before we enter to loop body:


```csharp
unsafe int* VectorizedPartitionInPlace(int* left, int* right)
{
    // ... omitted for brevity
    var writeLeft = left;
    var writeRight = right - N - 1; // <- Why the hate?
    var tmpLeft = _tempStart;
    var tmpRight = _tempEnd - N;

    PartitionBlock(left,          P, ref tmpLeft, ref tmpRight);
    PartitionBlock(right - N - 1, P, ref tmpLeft, ref tmpRight);

    var readLeft  = left + N;
    var readRight = right - 2*N - 1;
```

Here's a simple fix: Just moving the pointer declaration closer to the loop body seems to convince the JIT that we can all be friends once more:

```csharp
unsafe int* VectorizedPartitionInPlace(int* left, int* right)
{
    // ... omitted for brevity
    var tmpLeft = _tempStart;
    var tmpRight = _tempEnd - N;

    PartitionBlock(left,          P, ref tmpLeft, ref tmpRight);
    PartitionBlock(right - N - 1, P, ref tmpLeft, ref tmpRight);

    var writeLeft = left;
    var writeRight = right - N - 1; // <- Oh, so now we're cool?
    var readLeft  = left + N;
    var readRight = right - 2*N - 1;
```

The asm is slightly cleaner:

```nasm
mov     r8,rax        ; ✓ copy readLeft
sub     r8,r15        ; ✓ subtract writeLeft
mov     r9,r8         ; ✘ wat?
sar     r9,3Fh        ; ✘ wat?1?
and     r9,3          ; ✘ wat?!?!?
add     r8,r9         ; ✘ wat!?!@#
sar     r8,2          ; ✘ wat#$@#$@
mov     r9,rsi        ; ✓ copy writeRight
sub     r9,rcx        ; ✓ subtract readRight
mov     r10,r9        ; ✘ wat?1?
sar     r10,3Fh       ; ✘ wat?!?!?
and     r10,3         ; ✘ wat!?!@#
add     r9,r10        ; ✘ wat#$@#$@
sar     r9,2          ; ✘ wat^%#^#@!
cmp     r8,r9         ; ✓ finally, comapre!
```

It doesn't look like much, but we've managed to remove two memory accesses from the loop body (the read, shown above and a symmetrical write to the same stack variable/location towards the end of the loop).
It's also clear, at least from my comments that I'm not entirely pleased yet, so let's move on to...

#### JIT bug 2: not optimizing pointer difference comparisons

Calling this one a bug might be stretch, but in the world of the JIT, sub-optimal code generation can be considered just that. The original code performing the comparison is making the JIT (wrongfully) think that we want to perform `int *` arithmetic for `readLeft - writeLeft` and `writeRight - readRight`. In other words: The JIT is starts with generating code subtracting the two pointers, generating a `byte *` difference for each pair, which is great (I marked that with checkmarks in the listings), but then goes on to generate extra code converting those differences to `int *` units: so lots of extra arithmetic operations. We just care if one side is larger than the other. This is similar to converting two distance measurements taken in `cm` to `km` to compare which is greater. Clearly redundant.

To work around this disappointing behaviour, I wrote this instead:

```csharp
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight) {
    // ...
} else {
    // ...
}
```

By doing this sort of seemingly useless casting 4 times, we get the following asm generated:

```nasm
mov rcx, rdi  ; ✓ copy readRight
sub rcx, r12  ; ✓ subtract writeLeft
mov r9, rdi   ; ✓ copy writeRight
sub r9, r13   ; ✓ subtract readRight
cmp rcx, r9   ; ✓ compare
```

It doesn't take a degree in reverse-engineering asm code to figure out this was a good idea!  
Casting each pointer to `byte *` coerces the JIT to do our bidding and just perform a simpler comparison.

#### JIT Bug 3: Removing extra instructions

I discovered another missed opportunity in the pointer mutation code in the inlined partitioning block. When we update two `write*` pointers, we are really updating an int pointer with the result of the `PopCount` intrinsic:

```csharp
var popCount = PopCount(mask);
writeLeft += 8U - popCount;
writeRight -= popCount;
```

Unfortunately, the JIT isn't smart enough to see that it would be wiser to left shift `popCount` once by `2` (e.g. convert to `byte *` distance)  and reuse that left-shifted value **twice** while mutating the two pointers.
Again, uglifying the originally clean code into the following god-awful mess get's the job done:

```csharp
var popCount = PopCount(mask) << 2;
writeRight = ((int *) ((byte *) writeRight - popCount);
writeLeft =  ((int *) ((byte *) writeLeft + 8*4U - popCount);
```

I'll skip the asm this time, it's pretty clear from the C# that we pre-left shift (or multiply by 4) the popCount result before mutating the pointers.
We're now generating slightly denser code by eliminating a silly instruction from a hot loop.

All 3 of these workarounds can be seen on my repo in the [research branch](https://github.com/damageboy/VxSort/tree/research). I kept this pretty much as-is under [01_DoublePumpMicroOpt](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/01_DoublePumpMicroOpt.cs).
Time to see whether all these changes actuall help in terms of performance:

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

This is better! The improvement is *very* measurable. Too bad we had to uglify the code to get here, but such is life. Our results just improved by another ~4-9% across the board.  
If this is the going rate for ugly, I'll bite the bullet :)

### Get rid of localsinit flag on all methods: :+1:

While this isn't "coding" per-se, I think it's something that is worthwhile mentioning in this series: Historically, the C# compiler emits the `localsinit` flag on all methods that declare local variables. This flag, which can be clearly seen in .NET MSIL disassembly instructs the JIT to generate machine code that zeros out the local variables as the function starts executing. While this isn't a bad idea in itself, it is important to point out that this is done even though the compiler is already rather strict and employs definite-assignment analysis to avoid having uninitialized locals at the source-code level to begin with... Sounds confusing? Redundant? I thought so too!  
To be clear: Even though we are *not allowed* to use uninitialized variables in C#, and the compiler *will* throw those `CS0165` errors at us and insist that we initialize everything like good boys and girls, the emitted MSIL will still instruct the JIT to generate **extra** code, essentially double-initializing locals, first with `0`s thanks to `.localinit` before we get to initialize them from C#. Naturally this adds more code to decode and execute, which is not OK in my book. This is made worse by the fact that we are discussing this extra code in the context of a recursive algorithm where the partitioning function is called hundreds of thousands of times for sizeable inputs (you can go back to the 1<sup>st</sup> post to remind yourself just how many times the partitioning function gets called per each input size, hint: it's alot!).

There is a [C# language proposal](https://github.com/dotnet/csharplang/blob/master/proposals/skip-localsinit.md) that seems to be dormant about allowing developers to get around this weirdness, but in the meantime I devoted 5 minutes of my life to use the excellent [`LocalsInit.Fody`](https://github.com/ltrzesniewski/LocalsInit.Fody) weaver for [Fody](https://github.com/ltrzesniewski/LocalsInit.Fody) which can re-write assemblies to get rid of this annoyance. I encourage you to support Fody through open-collective as it really is a wonderful project that serves so many backbone projects in the .NET World.

At any rate, we have lots of locals, and we are after all implementing a recursive algorithm, so this has a substantial effect on our performance:



Not bad: a 1%-3% improvement (especially for higher array sizes) across the board for practically doing nothing...

### Selecting a good `InsertionSort` threshold: :+1:

I briefly mentioned this at the end of the 3<sup>rd</sup> post: While it made sense to start with the same threshold, of `16` that `Array.Sort` uses to switch from partitioning into small array sorting, there's no reason to assume this is the optimal threshold for our partitioning function. I tried 24, 32, 40, 48 on top of 16, and this is what came out:

<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/insertion-sort-threshold.svg"></object>

While it is clear that this is making a world of difference, this is the sort of threshold tuning best left as a last step, as we still have a long journey to optmize both the partitioning and replacing the small sorting. Once we exhaust all other options, the dynamics and therefore the optimal cut-off point between both methods will change anyway. We'll stick to 32 for now and come back to this later.

### Aligning to CPU Cache-lines: :+1:

In modern hardware, CPUs *might* access memory more efficiently when it is naturally aligned: in other words, when its *address* is a multiple of some magical constant. This constant is usually the machine word size, which is 4/8 bytes on 32/64 bit machines. These constants are normally related to how the CPU is physically wired and constructed internally.
While this is the generally accepted definition of alignment, with truly modern hardware, these requirements have become increasingly relaxed: Historically, older processors used to be very limited, either disallowing or severly limiting performance, with non-aligned access. To this day, very simple micro-controllers (like the ones you might find in IoT devices, for example) will exhibit such limitations around memory alignment forcing memory access to conform to multiples of 4/8.  
With truly modern/high-end CPUs (e.g. Intel/AMD CPUs like you are most probably using, especially in the context of this series) most programmers can afford to ignore this issue. The last decade or so worth of modern processors are oblivious to this problem per-se, as long as we access memory within a **single cache-line**, or 64-bytes on almost any modern-day processors.

What is a cache-line? I'm actively trying to **not turn** this post into a detour about computer micro-architecture, and caches have been covered so many times before by more apt writers than I am, so I'll just do the obligatory single paragraph reminder where we recall that CPUs don't directly communicate with RAM, as it is too slow; instead they read and write from internal, on-die, CPU caches, which are much faster, and organized in multiple levels (L1/L2/L3 caches, to name them). Each level is usually larger in size and slightly slower in terms of latency. When the CPU does access memory, it communicates with the cache subsystem, and it never does so in small units, even if our code is reading a single byte. Each processor comes with its definition of a minimal cache read/write unit, called a cache-line. Coincidentally, since this is, perhaps, the single most ironed out micro-arichtectural design issue with CPUs, it should come as no surprise that almost all modern CPUs, regardless of their manufacturer, seem to have converged to very similar cache designs and cache-line definitions: magically, almost all modern day hardware uses 64-bytes as that golden number.

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/cacheline-boundaries.svg"></object>

What happens when, lets say, our read operations end up **crossing** cache-lines? This literally causes the CPU to issue *two* read operations directed at the load ports/cache units. This sort of cache-line crossing read does have a sustained effect on perfromance[^0].  
For example, when we process a single array sequentially, reading 4-bytes at a time, if for some reason, our starting address is *not* divisable by 4, cross cache-line reads would occur at a rate of 4/64 or 6.25% of reads. Even this pretty small rate of cross-cacheline access usually remains *theoretical* for a combination of reasons: In most in programming languages, C# included, both the default memory allocator and the JIT/Compiler work in tandem to avoid this potential issue by making sure allocated memory is aligned to machine word size on the one hand, and by adding/padding bytes within our classes and structs in-between members, where needed, to make sure that individual members are also aligned to 4/8 bytes.  
So far, I’ve told you why/when you *shouldn’t* care about this. This was my way of both easing you into the topic and helping you feel not so bad if this is news to you. You really can afford *not to know* this and not pay any performance penalty, for the most part.  
Unfortunately, this is not true for `Vector256<T>` sized reads, which are 32 bytes wide (256 bits / 8). And this is doubly not true for our partitioning problem:

* The memory given to us for partitioning/sorting is almost *never* aligned to 32-bytes, except for stupid luck, since the allocator doesn’t care about that sort of alignment.
* Even if it were aligned, it would do us little good: The allocator, at best, would align the **entire** array to 32 bytes, but once we've performed a single partition operation, the next sub-division, inherent with QuickSort would be determined by the actual (e.g. random) data. There is no way we will get lucky enough that every partition will be 32-byte aligned.

Now that it is clear that we won’t be aligned to 32-bytes, We understand that when we go over the array sequentially (left to right and right to left as we do) issuing 32-byte reads on top of a 64-byte cache line, we end up reading across cache-lines every **other** read! Or at a rate of 50%! This just escalated from being "...generally not a problem" into a "Houston, we have a problem" very quickly.

Fine, we have a problem, the first step is acknowleding/accepting, so I'm told, so let’s consider our memory access patterns when reading/writing with respect to alignment:

* For writing, we're all over the place, we always advance the write pointers according to how the data was partitioned, e.g. it is data dependent, and there is little we can say about our write addresses. Also, Intel CPUs have a specific optimization for this in the form of store buffers, which I'll refrain from describing here; the bottom line is we can’t/don't need to care about writing.
* For reading, the situation is different: We *always* advance the read pointers by 8 elements (32-bytes) on the one hand, and we actually have a special intrinsic: `Avx.LoadAlignedVector256()`[^1] that can help us ensure that we are indeed reading from aligned memory.

Can something about these cross-cacheline reads? Yes! and initially, I did get something "working" quickly: remember that we need to deal with the remainder of the array anyway, and we've been doing that towards the end of our partitioning code thus far. We can move that code from the end of our partitioning function, to the beginning while also modifying it to partition with scalar code until both `readLeft`/`readRight` pointers are aligned to 32 bytes.  
This means we would do a little more scalar work:

* Previously we had 0-7 elements left as a remainder for scalar partitioning per partition call.
  * `3.5` elements on average.
* By aligning from the partition's outer rims *inwards* we will have 0-7 elements on both sides to partition with scalar code...
  * So `3.5 x 2 == 7` elements on average.

In other words, doing this sort of pre-alignment inwards is an optimization with a trade-off: We will end up with more scalar work than before on the one hand (which is unfortunate), but on the other hand, we can change the vector loading code to use `Avx.LoadAlignedVector256()` and *know for sure* that we will no longer be causing the CPU to do cross cache-line reads (The latter being the performance boost).  
I understand that some smart-ass readers will want to quickly point out that adding 3.5 scalar operations doesn't sound like much of a trade off, but that isn't entirely true: each scalar comparison comes with a likely branch mis-prediction, so it has a higher cost than what you are initially pricing in; also, just as importantly don't forget that this is a recursive function, with ever decreasing partition sizes. If you go back to the initial stats we collected in previous posts, you'll be quickly reminded that we partition upwards of 300k times for 1 million element arrays, so this scalar work does pile up...

I won't bother showing the entire code listing for [`02_DoublePumpAligned.cs`](https://github.com/damageboy/VxSort/blob/research/VxSortResearch/Unstable/AVX2/Happy/02_DoublePumpAligned.cs), but I will show the rewritten scalar partition block; originally it was right after the double-pumped loop and looked like this:

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

### Simplifying the branch :+1:

I'm kind of ashamed at this, since I was literally staring at this line of code and optimizing around it for such an extended duratrion without stopping to really think about what it IS that I'm doing. Really. So let's go back to our re-written branch from a couple of paragraphs ago:

```csharp
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight) {
    // ...
} else {
    // ...
}
```

I've been describing this condition both in animated or code form in the previous part, explaining that by it is critical for my double-pumping to work, to figure out which side we need to read from next so we never end-up overwriting data we didn't have a chance to read and partition yet. All in the name of keeping the partitioning in-place. Except I've been overcomplicating the actual condition!

At some, admittedly late stage, it hit me: given the setup, where we've made 8 elements worth of space available by partitioning them away into the temporary memory, we always pick one side to read from, so far this has been the left side (It doesn't matter which side, it ended up being the left side due to the condition being `<=` rather than `<`). Once we've done that, we've enlarged that side from 8 to 16 elements worth of breathing space temporarily; Once partitioning is complete, the left side is either back at having 8 elements of space (in the extreme case that all elements were smaller than the selected pivot) or more. Since those are the true dynamics, why do we even bother comparing both heads and tails of each respective side?  
We could simplify the branch and instead compare the right head+tail pointer distance to see if it is smaller than the magical number 8 or not!  
When it is smaller than 8, we read from the right side, since it is in danger of being over-written, otherwise, we go back to reading from the left side, since the only other option is that both sides have 8 elements at each side. Naturally this ends up being a simpler branch to encode and execute:

```csharp
int* nextPtr;
if (((byte *) writeRight - (byte *) readRight) < N * sizeof(int)) {
        // ...
} else {
        // ...
}
```

This branch has the same result as the previous one, but it is less taxing in a few ways:
* Less instructions to execute
* Less data dependencies (we don't need to wait for the `writeLeft`/`readLeft` pointer mutation to complete inside the CPU)

One interesting question that I personally did not know the answer too before hand was: would this reduce branch mis-predictions?  There's
only one way of finding out, isn't there? Let's fire up perf and compare the `03_.....cs` version to the `04_....cs` version with respect to branch mis-predictions. Will it budge?






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
