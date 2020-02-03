---
title: "This Goes to Eleven (Pt. 4/∞)"
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
date: 2020-02-01 08:26:28 +0300
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

Calling this one a bug might be stretch, but in the world of the JIT, sub-optimal code generation can be considered just that. The original code performing the comparison is making the JIT (wrongfully) think that we want to perform `int *` arithmetic for `readLeft - writeLeft` and `writeRight - readRight`. In other words: The JIT starts with generating code subtracting both pointer pairs, generating a `byte *` difference for each pair; which is great (I marked that with checkmarks in the listings). Then, it goes on to generate extra code converting those differences into `int *` units: so lots of extra arithmetic operations. This is simply useless: we just care if one side is larger than the other. This is similar to converting two distance measurements taken in `cm` to `km` just to compare which one is greater.  
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

#### JIT Bug 3: Updating the `write*` pointers more efficiently

I discovered another missed opportunity in the pointer update code at the end of our inlined partitioning block. When we update the two `write*` pointers, our intention is to update two `int *` values with the result of the `PopCount` intrinsic:

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

### Get rid of `localsinit` flag on all methods: :+1:

While this isn't "coding" per-se, I think it's something that's worthwhile mentioning in this series: Historically, the C# compiler emits the `localsinit` flag on all methods that declare local variables. This flag, which can be clearly seen in .NET MSIL disassembly, instructs the JIT to generate machine code that zeros out the local variables as the function starts executing. While this isn't a bad idea in itself, it is important to point out that this is done even though the C# compiler is already rather strict and employs definite-assignment analysis to avoid having uninitialized locals at the source-code level to begin with... Sounds confusing? Redundant? I thought so too!  
To be clear: Even though we are *not allowed* to use uninitialized variables in C#, and the compiler *will* throw those `CS0165` errors at us and insist that we initialize everything like good boys and girls, the emitted MSIL will still instruct the JIT to generate **extra** code, essentially double-initializing locals, first with `0`s thanks to `localinit` before we get to initialize them from C#. Naturally, this adds more code to decode and execute, which is not OK in my book. This is made worse by the fact that we are discussing this extra code in the context of a recursive algorithm where the partitioning function is called hundreds of thousands of times for sizeable inputs (you can go back to the 1<sup>st</sup> post to remind yourself just how many times the partitioning function gets called per each input size, hint: it's a lot!).

There is a [C# language proposal](https://github.com/dotnet/csharplang/blob/master/proposals/skip-localsinit.md) that seems to be dormant about allowing developers to get around this weirdness, but in the meantime, I devoted 5 minutes of my life to use the excellent [`LocalsInit.Fody`](https://github.com/ltrzesniewski/LocalsInit.Fody) weaver for [Fody](https://github.com/ltrzesniewski/LocalsInit.Fody) which can re-write assemblies to get rid of this annoyance. I encourage you to support Fody through open-collective as it is a wonderful project that serves so many backbone projects in the .NET World.

At any rate, we have lots of locals, and we are after all implementing a recursive algorithm, so this has a substantial effect on performance:



Not bad: a 1%-3% improvement (especially for larger array sizes) across the board for practically doing nothing...

### Selecting a better `InsertionSort` threshold: :+1:

I briefly mentioned this at the end of the 3<sup>rd</sup> post: While it made sense to start with the same threshold, of `16` that `Array.Sort` uses to switch from partitioning into small array sorting, there's no reason to assume this is the optimal threshold for our partitioning function. I tried 24, 32, 40, 48 on top of 16, and this is what came out:

<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/insertion-sort-threshold.svg"></object>

While it is clear that this is making a world of difference, this is the sort of threshold tuning best left as a last step, as we still have a long journey to optmize both the partitioning and replacing the small sorting. Once we exhaust all other options, the dynamics and therefore the optimal cut-off point between both methods will change anyway. We'll stick to 32 for now and come back to this later.

### Prefetching: :-1:

I tried using prefetch intrinsics to give the CPU early hints as to where we are reading memory from.

Generally speaking, prefetching should be used to make sure the CPU always reads some data from memory into the cache ahead of the actual time we would require it so that the CPU never stalls waiting for memory, which is very slow. The bottom line is that having to wait for RAM is a death sentence, but even having to wait for L2 cache (14 cycles) when your entire loop's throughput is around 9 cycles is unpleasant. With prefetch intrinsics we can prefetch all the way to L1 cache, or even specify the target level as L2, L3.
But do we actually need to prefetch? Well, there is no clear cut answer except than trying it out. CPU designers know all of the above just as much as we do, and the CPU already attempts to prefetch data. But it's very hard to know when it might need our help. Adding prefetching instructions puts more load on the CPU as we're adding more instructions to decode & execute, while the CPU might already be doing the same work without us telling it. This is the key consideration we have to keep in mind when trying to figure out if prefetching is a good idea. To make matters worse, the answer can also be CPU model specific... In our case, prefetching the *writable* memory **makes no sense**, as our loop code mostly reads from the same addresses just before writing to them in the next iteration or two, so I mostly focused on trying to prefetch the next read addresses.

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

I'm kind of ashamed at this: I had been literally staring at this line of code and optimizing around it for such a long time without stopping to really think about what it **was** that I'm really trying to do. Let's go back to our re-written branch from a couple of paragraphs ago:

```csharp
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight) {
    // ...
} else {
    // ...
}
```

I've been describing this condition both in animated and code form in the previous part, explaining how for my double-pumping to work, I have to figure out which side we *must* read from next so we never end-up overwriting data we didn't have a chance to read and partition yet.  
All of this is happening in the name of performing in-place partitioning. However, I've been over-complicating the actual condition!
At some, admittedly late stage, it hit me, so let's play this out step by step:

1. We always start with the setup I've described before, where we make `8` elements worth of space available on **both** sides, by partitioning them away into the temporary memory.
2. When we get into the main partitioning loop, we initially pick one specific side to read from: so far, this has always been the left side (It doesn't matter which side it is, but it ended up being the left side arbitrarily due to the condition being `<=` rather than `<`).
3. Given all of the above, we always *start* reading from the left, increasing the "breathing space" on that left side from `8` to `16` elements temporarily.
4. Once our trusty ole' partitioning block has finished, we can pause for a second to think how both sides look:
  * The left side either has:
    * `8` elements of space (in the less likely, yet possible case that all elements read from it were smaller than the selected pivot) -or-
    * It has more than `8` elements of "free" space.
  * In the first case, where the left side is now back to 8 elements of free space, the right side also has `8` elements of free space, since nothing was written on that side.
  * In all other cases, the left side has more than `8` elements of free space, and the right side has less than `8` elements of free space, by definition.
5. Since those are the true dynamics, why do we even bother comparing **both** heads and tails of each respective side?  

The answer to that last question is: We don't have to! We could simplify the branch by comparing only the right head+tail pointer distance to see if it is smaller than the magical number `8` or not!
This new condition would be just as good at serving the original *intent* (which is: "don't end up overwriting unread data") as the more complicated branch we used before...  
When the right side has less than `8` elements, we *have to* read from the right side in the next round, since it is in danger of being over-written, otherwise, the only other option is that both sides are back at 8-elements each, and we should go back to reading from the left side again, essentially going back to our starting setup condition as described in (1). It's kind of silly, and I really feel bad it took me 4 months or so to see this. Naturally this ends up being a simpler branch to encode and execute:

```csharp
int* nextPtr;
if (((byte *) writeRight - (byte *) readRight) < N * sizeof(int)) {
        // ...
} else {
        // ...
}
```

This branch is just as "correct" as the previous one, but it is less taxing in a few ways:
* Less instructions to execute
* Less data dependencies for CPU to wait for (we don't need to wait for the `writeLeft`/`readLeft` pointer mutation and then subtraction to complete)

Naturally this ends up being faster, and we have BDN results to show that:

One interesting question that I personally did not know the answer to beforehand was: would this reduce branch mis-predictions? There's only one way of finding out, isn't there? Let's fire up `perf` and compare two versions of the code where this simplified branch is the only difference, we'll compare the `03_.....cs` version to the `04_....cs` version with respect to branch mis-predictions. Will it budge?

### Packing the Permutation Table, 1<sup>st</sup> attempt: :+1:

Ever since I started with this little time-sucuubus of a project, I was really annoyed at the way I was encoding the permutation tables. To me, wasting 8kb worth of data, or more specifically, wasting 8kb worth of precious L1 cache in the CPU for the permutation entries was super wasteful. My emptional state regarding this was made worse when you stop to consider that out of each 32-byte permutation entry, we were only really using 3 bits x 8 elements, or 24 bits of usable data. To be completely honest, I probably made this into a bigger problem in my head, imagining how the performance was suffering from this, than what it really is in reality, but we don't always get to to choose our made-up enemies. sometimes they choose us. 

My first attempt at packing the permutation entries was to try and use a specific Intel intrinsic called [`ConvertToVector256Int32` / `VPMOVZXBD`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx2.converttovector256int32?view=netcore-3.1) that can read a 64-bit value while expanding it into 8x32bit values inside a `Vactor256<T>` register. The basic idea was that I would back the permutation entries as 64-bits per single entry instead of 256-bits which is what I've been using thus far. This would reduce the size of the entire permutation entry from 8kb to 2kb, which is a nice start. Unfortunately, this initial attempt went south as it got hit by a [JIT bug](https://github.com/dotnet/runtime/issues/12835). When I tried to circumvent that bug, the results didn't look much better, even slightly slower so I kind of left the code in a sub-optimal state and forgot about it.
Luckily I did revisit this at a later stage, when the bug was properly fixed, and to my delight, once the JIT was encoding this instruction correctly and efficiently, things start working smoothly.

I ended up encoding and aligning a second permutation table, and by using the correct `ConvertToVector256Int32` which can directly accept a pointer to memory, performance did improve in a measureable way:


### Packing the Permutation Table, 2<sup>nd</sup> attempt: :-1:

Next, I tried to pack the permutation table even further, going from 2kb to 1kb of memory, by packing the 3-bit entries even further into a single 32-bit value.
The packing is the easy part, but how would we unpack this 32-bit compressed entries? Well, with intrinsics of course, if nothing else, it was worth it so I could do this:
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
```

And my unpacking code relied on using the [`ParallelBitDposit / PDEP`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.bmi2.x64.parallelbitdeposit?view=netcore-3.1#System_Runtime_Intrinsics_X86_Bmi2_X64_ParallelBitDeposit_System_UInt64_System_UInt64_), which I've accidentaly covered in more detail in a [previous post]({% post_url 2018-08-19-netcoreapp3.0-intrinsics-in-real-life-pt2 %}#pdep---parallel-bit-deposit): 

```csharp
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
* Convert (move) it to a 128-bit SIMD register using `Vector128.CreateScalarUnsafe`.
* Go back to using a different variant of [`ConvertToVector256Int32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_cvtepi8_epi32&expand=1532) (`VPMOVZXBD`) that takes 8-bit elements from a 128-bit wide register and expands them into integers in a 256 bit registers.

In short, we chain 3 extra instructions, but save an additional 1KB of cache. Was it worth it?  
I wish I could say with a complete and assured voice that it was, but the truth is that it had only very little positive effect, if any:

While we end up using 1kb of cache instead of 2kb, the extra instructions end up delaying and costing us more.  
I still think this optimization might do some good, but for this to make a bigger splash we need to be in a situation were there is more pressure on the cache, for the extra latency to be worth it. For now we can simply chalk it up as a failure.

### Skipping some permutations: :-1:

There are very common cases where performing the permutation is completely un-needed. This means that almost the entire permutation block can be skipped:
* No need to load the perutation entry
* Or perform the permutation

To be percise, there are exactly 9 such cases in the permutation table, whenever the all the `1` bits are already grouped in the upper (MSB) part of the `mask` value in our permutation block, the values are:
* `0b11111111`
* `0b11111110`
* `0b11111100`
* `0b11111000`
* `0b11110000`
* `0b11100000`
* `0b11000000`
* `0b10000000`
* `0b00000000`

I thought it might be a good idea to detect those cases using a switch case or some sort of other intrinsics based code, while it did work, the extra branch and associated branch mis-prediction didn't make this worth while or yield any positive result. The simpler code which always permutes did just as good. Oh well, it was worth the attempt...

### Reordering instructions: :-1:

I also tried reordering some instructions so that they would happen sooner inside the loop body. For example: moving the `PopCount`ing to happen sooner (immediately after we calculate the mask).

None of these attempts helped, and I think the reason is that CPU already does this on its own, so while it sounds logical that this should happen, it doesn't seem to help when we change the code to do it given that the CPU already does it all by itself without our generous help.

### Getting intimate with x86 for fun and profit: :+1:

I know the title sounds cryptic, but x86 is just weird, and I wanted to make sure you're mentally prepared for some weirdness in our journey to squeeze a bit of extra performance. We need to remember that this is a 40+ year-old CISC processor made in an entirely different era:

![Your Father's LEA](../assets/images/your-fathers-lea.svg)

The last optimization I will go over in this post is about generating slightly denser code in our vectorized block. The idea here is to trigger the JIT to encode the pointer update code at the end of our vectorized partitioning block with the more space-efficient `LEA` instruction.

To better explain this, We'll start by going back to the last 3 lines of code I presented at the top of *this* post, as part of the so-called micro-optimized version. Here is the C#:

```csharp
    // end of partitioning block...
    var popCount = PopCnt.PopCount(mask);
    writeRight = (int*) ((byte*) writeRight - popCount);
    writeLeft  = (int*) ((byte*) writeLeft + (8U << 2) - popCount);
```

If we look at the corresponding disassembly for this code, it looks quite verbose. Here it is with some comments, and with the machine-code bytes on the right-hand side:

```nasm
;var popCount = PopCnt.PopCount(mask);
popcnt  r8d,r8d ; F3450FB8C0
shl     r8d,2   ; 41C1E002

;writeRight = (int*) ((byte*) writeRight - popCount);
mov     r9d,r8d ; 458BC8
sub     rcx,r9  ; 492BC9

;writeLeft  = (int*) ((byte*) writeLeft + (8U << 2) - popCount);
add     r12,20h ; 4983C420
mov     r8d,r8d ; 458BC0
sub     r12,r8  ; 4D2BE0
```

If we count the bytes, everything after the `PopCount` instruction is taking `20` bytes in total: `4 + 3 + 3 + 4 + 3 + 3` to complete both pointer updates.

The motivation behind what I'm about to show is that we can replace all of this code with a shorter sequence, taking advantage of x86's wacky memory addressing, by tweaking the C# code ever so slightly. This, in turn, will enable the C# JIT, which is already aware of these x86 shenanigans, and is capable of generating the more compact code when it encounters the right constructs at the MSIL/bytecode level.  
We succeed here if and when we end up using one `LEA` instruction for each pointer modification.

What is `LEA` you ask? **L**oad **E**ffective **A**ddress is an instruction that happens to exposes the full extent of x86's memory addressing capabilities in a single instruction, allowing us to encode rather complicated mathematical/address calculations with a minimal set of bytes, abusing the CPUs address calculation units, finally storing the result of that calculation back to a register.

But what can the address calculation units do for us? We need to learn just enough about what it can and cannot do for us to succeed in milking some performance out of `LEA`. Out of curiosity, I went back in time to find out *when* the memory addressing scheme was defined/last changed, and to my surprise, I found out it was *much later* than what I had originally thought: Intel last *expanded* the memory addressing semantics as late as **1986**! Of course this was later expandd again by AMD when they introduced `amd64` to propel x86 from the 32-bit dark-ages into the brave world of 64-bit processing, but that was merely a machine-word expansion. I'm happy I researched this bit of history for this post because I found [this scanned 80386 manual](../assets/images/230985-001_80386_Programmers_Reference_Manual_1986.pdf):

<center>
<div markdown="1">
[![80386](../assets/images/80386-manual.png)](../assets/images/230985-001_80386_Programmers_Reference_Manual_1986.pdf)
</div>
</center>

In this refernce manual, the "new" memory addressing semantics are described in section `2.5.3.2` on page `2-18`, reprinted here for some of its 1980s era je ne sais quoi:  

![x86-effective-address-calculation](../assets/images/x86-effective-address-calculation-transparent.png)

Figure `2-10` in the original manual does a very good job explaining the components and machinery that goes into a memory address calculation in x86, Here it is together with my plans to abuse it:
* Base register; This will be our pointer that we want to modify: `writeLeft` and `writeRight`.
* Index: the `PopCount` result, in some form.  
  The index has to be *added* to the base register, the operation will always be addition; of course nothing prevents us from adding a negative number...
* Scale: The `PopCount` result needs to be multiplied by 4, we'll do it with the scale.
  The scale is limited to be one of `1/2/4/8`, but *for us* this is not a limitation.
* Displacement: Some other constant we can tack on to the address calculation. The displacement can be 8/32 bits and is also always used with an addition operation.  
  *But:* and this is an **important** "but": nothing is preventing the compiler from encoding negative numbers as the displacemnt; again, taking advantage of signed addition, effectively turning this into a subtraction.

The actual code change is super-simple. But without all this pre-amble it wouldn't make sense, here it is:
```csharp
    // ...
    var popCount = -PopCnt.PopCount(mask);
    writeRight = writeRight + popCount;
    writeLeft  = writeLeft + popCount + 8;
```

You must think I'm joking, but really, this is it. By pre-negating the `PopCount` result and writing the simpler code, without all the pre-shifting optimization fanciness, we get this beatiful assembly code automatically generated for us by the JIT:

```nasm
popcnt  rdi,rdi             ; F3480FB8FF
neg     rdi                 ; 48F7DF
lea     rax,[rax+rdi*4]     ; 488D04B8
lea     r15,[r15+rdi*4+20h] ; 4D8D7CBF20
```

The new version is taking `3 + 4 + 5` or `12` bytes in total, to complete both pointer updates. So it's clearly denser. It is important to point out that this reduces the time taken by the CPU to fetch and decode these instructions, and not necessarily the time to execute the underlying calculation.

How does it improve the performance?

TABLE GOES HERE!!!

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

---

[^0]: Most modern Intel CPUs can actually address the L1 cache units twice per cycle, that means they can actually ask it to read two cache-line as the same time. But this still causes more load on the cache and bus, and we must not forget that we will be reading an additional cache-line for our permutation block...
[^1]: This specific AVX2 intrinsic will actually fail if/when used on non-aligned addresses. But it is important to note that it seems it won’t actually run faster than the previous load intrinsic we’ve used: `AVX2.LoadDquVector256` as long as the actual addresses we pass to both instructions are 32-byte aligned. In other words, it’s very useful for debugging alignment issues, but not that critical to actually call that intrinsic! 
