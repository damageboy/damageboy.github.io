---
title: "Trumping Array.Sort with AVX2 Intrinsics (Part 4/6)"
header:
  image: /assets/images/coreclr-clion-header.jpg
hidden: true
date: 2019-08-20 11:26:28 +0300
classes: wide
#categories: coreclr intrinsics vectorization quicksort sorting
---

I ended up going down the rabbit hole re-implementing `Array.Sort()` with AVX2 intrinsics, and there’s no reason I should store all the agony inside (to be honest: I had a lot of fun with this). I should probably attempt to have a serious discussion with CoreCLR  / CoreFX people about starting a slow process that would end with integrating this code into the main C# repos, but for now, let's get in the ring and show what AVX/AVX2 intrinsics can really do for a non-trivial problem, and even discuss potential improvements that future CoreCLR versions could bring to the table.

Since there’s a lot to over go over here, I’ll split it up into a few parts:

1. In [part 1](2019-08-08-trumping-arraysort-with-avx2-pt1.md), we did a short refresher on `QuickSort` and how it compares to `Array.Sort()`. If you don’t need any refresher, you can skip over it and get right down to part 2 and onwards , although I really recommend skimming through, mostly because I’ve got really good visualizations for that should be in the back of everyone’s mind as we’ll be dealing with vectorization & optimization later.
2. In [part 2](2019-08-08-trumping-arraysort-with-avx2-pt2.md), we went over the basics of Vectorized HW Intrinsics, discussed vector types, and a handful of vectorized instructions we’ll actually be using in part 3, but we still weren't sorting anything.
3. In part 3 we go through the initial code for the vectorized sorting and we’ll finally start seeing some payoff. We’ll finish with some agony courtesy of CPU’s Branch Predictor, just so we don't get too cocky.
4. In this part, we go over a handful of optimization approaches that I attempted trying to get the vectorized partition to run faster, we'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of 100% of the remaining scalar code, by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization and gain a considerable amount of performance / efficiency in the process.
6. Finally, in part 6, I’ll list the outstanding stuff / ideas I have for getting more juice and functionality out of my vectorized code.

## (Trying) to squeeze some more vectorized  juice

I thought it would be nice to show a bunch of things I ended up trying to improve performance. Some worked, some not, but all were worth mentioning, so here goes:

### Dealing with small JIT hiccups [Worked]

One of the more annoying things I've discovered during this optimization process was the the JIT isn't really ready to do proper optimization with pointers.  
Consider these two following pieces of code, which we've shown before:

```csharp
if (readLeft   - writeLeft <= 
    writeRight - readRight) {
    current = LoadDquVector256(readLeft);
    readLeft += 8;
} else {
    current = LoadDquVector256(readRight);
    readRight -= 8;
}
```

While this was what I originally wrote, my new disillusionment with the JIT means I have to write this:

```csharp
if ((byte *) readLeft   - (byte *) writeLeft) <= 
    (byte *) writeRight - (byte *) readRight)) {
    current = LoadDquVector256(readLeft);
    readLeft += 8;
} else {
    current = LoadDquVector256(readRight);
    readRight -= 8;
}
```

Why bother casting 4 `int *` to a `byte *`. What does this serve?  
The original code made the JIT (wrongfully) think we are actually doing `int *` arithmetic for `readLeft - writeLeft` and `writeRight - readRight`. In other words: The JIT generated code to take the numerical pointer differences, and generated extra code to convert them to `int *` differences: so lots of `>> 2` operations.  
However, this is useless, we just care if one side is larger that the other, we don't care if this is done with `byte *` or `int *` comparisons... So by forcefully casting each pointer  to `byte *` we are "telling" the JIT that the comparison can be made without the superfluous shifts.

The same pattern (albeit slightly more convoluted) re-surfaced here:

```csharp
var popCount = PopCount(mask);
writeLeft += 8U - popCount;
writeRight -= popCount;
```

Here, the `popCount` result is used to increment two `int *` values. Unfortunately, the JIT isn't smart enough to see that it would be wiser to left shift `popCount` once by `2` (e.g. convert to `byte *` distance)  and reuse that value twice. Rewriting this to:

```csharp
var popCount = PopCount(mask) << 2;
writeRight = ((int *) ((byte *) writeRight - popCount);
writeLeft =  ((int *) ((byte *) writeLeft + 8*4U - popCount);
```

Again helped with generating slightly denser code by eliminating instructions from a hot loop.

But does it help?


| Method           | N        |           Mean |   Time / N | Ratio |
| ---------------- | -------- | -------------: | ---------: | ----: |
| ArraySort        | 100      |       1.164 us | 11.6393 ns |  1.00 |
| AVX2DoublePumped | 100      |       1.473 us | 14.7293 ns |  1.27 |
| ArraySort        | 1000     |      33.145 us | 33.1454 ns |  1.00 |
| AVX2DoublePumped | 1000     |      21.885 us | 21.8851 ns |  0.66 |
| ArraySort        | 10000    |     535.309 us | 53.5309 ns |  1.00 |
| AVX2DoublePumped | 10000    |     302.099 us | 30.2099 ns |  0.56 |
| ArraySort        | 100000   |   5,959.836 us | 59.5984 ns |  1.00 |
| AVX2DoublePumped | 100000   |   3,101.084 us | 31.0108 ns |  0.52 |
| ArraySort        | 1000000  |  69,337.724 us | 69.3377 ns |  1.00 |
| AVX2DoublePumped | 1000000  |  25,302.633 us | 25.3026 ns |  0.36 |
| ArraySort        | 10000000 | 802,422.112 us | 80.2422 ns |  1.00 |
| AVX2DoublePumped | 10000000 | 266,743.745 us | 26.6744 ns |  0.33 |

Sure does! The improvement is very measurable. Too bad we had to uglify the code to get here, but such is life. Our results just improved by another ~9% cross the board.  
If this is the going rate for ugly, I'll bite the bullet :)

### Aligning to CPU Cache-lines

Our memory access patterns are very different for reading/writing with respect to alignment:

* For writing, we're all over the place, we always advance the write pointers according to how the data was partitioned, e.g. it is data dependent, and there is little we can say about our write addresses. Also, Intel CPUs don't really have a special opcode for writing aligned data, so we don't care.
* For reading, the situation is different: We always advance the read pointers by 8 elements (32 bytes) on the one hand, and we actually have a special intrinsic: `Avx.LoadAlignedVector256()`.

Alignment, in general, is not super critical in Intel CPUs, although some people believe this in this myth, probably due to bad experience a decade ago. What is important to address, however, is when our read operations end up crossing cache-lines (which are 64 bytes on almost all modern HW). This literally causes the CPU to issue two operation w.r.t to the cache units. When we issue 4 byte reads over an array, this means this would happens at a rate of 4/64 or 6.25% of reads, but when we do this on `Vector256<T>` units, which are 32 bytes wide, thins means 50% of our reads end up doing two cache operations instead of 1. Not cool.

Can we do something about it? We sure can, since we need to deal with the remainder of the array anyway, we can move that code from the end of our partitioning function, to the beginning and also modify it to partition with scalar code until both `readLeft`/`readRight` pointers are aligned to 64 bytes, or a single cache-line.  
This means we would do a little more scalar work potentially on the one hand, but we can change all of the loading code to use `Avx.LoadAlignedVector256()`.

I won't bother showing all of the code, it's available here, but here is where we end up performance wise with it, after this additional optimization:



### Prefetching [Didn't work]

I tried using prefetch intrinsics to give the CPU early hints as to where we are reading memory from.

Generally speaking prefetching should be used to make sure the CPU always reads some data from memory to cache ahead of the actual time we would use it so we would not need to wait for memory to be available in the cache for our instructions to fire off inside the CPU. The reality is it's very hard to tell ahead of time if prefetching is useful, and how. And to really see if it helps you should probably test on more than one CPU. 

Whenever I modified `readLeft`, `readRight`, `writeLeft`, `writeRight` I immediately added code like this:

```csharp
writeRight = (int *) ((byte *) writeRight  - popCount);
writeLeft = (int *) ((byte *) writeLeft + (8U << 2) - popCount);
Sse.Prefetch0((byte *) writeLeft + 64);
Sse.Prefetch0((byte *) writeRight - 64);
```

This tells the CPU we are about to use data in `writeLeft + 64` (the next cache-line from the left) and `writeWrite -  64` (the next cache-line from the right) in the following iterations.

While this looks great on paper, the real world results of this were unnoticeable for me and even slightly negative. I think this is related to the fact that we are going over the same data over and over again and again as we re-partition the same data  in ever smaller partition. So the the prefetching did very little in actually avoiding memory stalls, while it did add more work for the CPU in terms of instruction decoding...  
Still it was worth a shot. 

### Packing the Permutation Table [Didn't work]

This following attempt yielded mixed results. In some cases (e.g. specific CPU models) it did better, on other is did worse, but all in all I still think it's interesting that it didn't do worse overall, and I haven't given up on it completely.

The original permutation table is taking up 32 bytes per element x 2<sup>8</sup> elements ⮞ 8kb in total. Just to be clear: **that's a lot!** For comparison, our entire CPU L1 data-cache is normally 32kb, and I'd sure rather have it store the actual data we're sorting, rather than my lookup table, right?

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

What does this little monstrosity do exactly? We pack the permutation bits (remember, we just need 3 bits per element, we have 8 elements, so 24 bits per permutation vector in total) into a single 32 bit value, then whenever we need to permute, we:

* Unpack the 32-bit values into a 64-bit value using [`ParallelBitDeposit`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=pdep&expand=1532,4152) from the `BMI2` intrinsics extensions.  
  In a stroke of luck I've already throughly covered it back in my `PopCount` series [here](2018-08-19-netcoreapp3.0-instrinsics-in-real-life-pt2.md).
* Converts (moves) it to a 128-bit SIMD register using `Vector128.CreateScalarUnsafe`.
* Finally uses yet another intrinsic [`ConvertToVector256Int32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_cvtepi8_epi32&expand=1532) that takes 8-bit elements from a 128-bit wide register and expand them into integers in a 256 bit registers.

In short, we chain 3 extra instructions, but save 7KB of cache. Is it worth it? 

| Method           | N        |           Mean |   Time / N | Ratio |
| ---------------- | -------- | -------------: | ---------: | ----: |
| ArraySort        | 100      |       1.167 us | 11.6733 ns |  1.00 |
| AVX2DoublePumped | 100      |       1.675 us | 16.7508 ns |  1.44 |
| ArraySort        | 1000     |      30.148 us | 30.1480 ns |  1.00 |
| AVX2DoublePumped | 1000     |      23.671 us | 23.6712 ns |  0.79 |
| ArraySort        | 10000    |     473.679 us | 47.3679 ns |  1.00 |
| AVX2DoublePumped | 10000    |     321.814 us | 32.1814 ns |  0.68 |
| ArraySort        | 100000   |   5,792.778 us | 57.9278 ns |  1.00 |
| AVX2DoublePumped | 100000   |   3,512.188 us | 35.1219 ns |  0.61 |
| ArraySort        | 1000000  |  69,494.254 us | 69.4943 ns |  1.00 |
| AVX2DoublePumped | 1000000  |  27,115.613 us | 27.1156 ns |  0.39 |
| ArraySort        | 10000000 | 802,518.169 us | 80.2518 ns |  1.00 |
| AVX2DoublePumped | 10000000 | 301,563.330 us | 30.1563 ns |  0.38 |

I wish I could say with a complete and assured voice that it does, but the truth is that it had only marginal benefits. While we end up using 256 bytes of cache instead of 8KB, the extra instructions still cost us quite a lot more. I did see this push perf up by 1%-2% compared to 8KB lookup tables, but I'm not sure it's such a big win. It might not be worth it, or maybe I still need to figure out how/why it can be. Time will tell.

### Skipping some permutations [Didn't work]

There are very common cases where permutation (and loading the permutation vector) is completely un-needed, to be exact there are 9 such cases in the permutation table, whenever the all the `1` bits are already grouped in the upper part of the register:

* 0b00000000
* 0b11111110
* 0b11111100
* 0b11111000
* 0b11110000
* 0b11100000
* 0b11000000
* 0b10000000
* 0b00000000

I thought it might be a good idea to detect those cases using a switch case or some sort of other intrinsics based code, while I did work, the extra branch and associated branch mis-prediction didn't see to yield any positive result. The simpler code which always permutes did just as good. Oh well, it was worth the attempt...

### Reordering instructions [Didn't work]

I also tried reordering some instructions so that they would happen sooner inside the loop body. For example: moving the `PopCount`ing to happen sooner (immediately after we calculate the mask).

None of these attempts helped, and I think the reason is that CPU already does this on its own, so while it sounds logical that this should happen, it doesn't seem to help when we change the code to do it given that the CPU already does it all by itself without our generous help.

### Mitigating the bad speculation

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

