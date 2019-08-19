---
title: "Trumping Array.Sort with AVX2 Intrinsics (Part 3/5)"
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
2. In [part 2](2019-08-08-trumping-arraysort-with-avx2-pt2.md), we went over the basics of Vectorized HW Intrinsics, discussed vector types, and a handful of vectorized instructions we’ll actually be using in part 3, but we still weren’t sorting anything.
3. In this part, part 3 I’ll present the initial code for the vectorized code and we’ll finally start seeing some payoff. We’ll also experience agony from the CPU’s Branch Predictor and try to overcome it with the limited tools we have in C# in 2019.
4. In part 4, we’ll see how we can almost get rid of 100% of the remaining scalar code, by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization and gain a considerable amount of performance / efficiency in the process.
5. Finally, in part 5, I’ll list the outstanding stuff / ideas I have for getting more juice and functionality out of my vectorized code.

## Vectorized Partitioning

It’s time we mash all the new knowledge we picked up in the last post about SIMD registers and instructions and do something useful with them. Here's the plan:

* First we take 8-element blocks, or units of `Vector256<int>`, and partition them with AVX2 intrinsics.
* Then we take the world: We reuse our block to partition an entire array by wrapping it with code that:
  * Preps the array
  * Loops over the data in 8-element chunks running our vectorized code block
  * Goes over the rest of the data and partitions the remainder using scalar code, since we're all out of 8-elements chunks, we need to finish off with just a bit of scalar partitioning, this is unfortunate but very typical for any vectorized code in the wild.

### AVX2 Partitioning Block

 Let’s start with the “simple” block:

```csharp
var P = Vector256.Create(pivot); 
...
var current = Avx2.LoadDquVector256(nextPtr);
var mask = (uint) Avx.MoveMask(
    Avx2.CompareGreaterThan(current, P).AsSingle()));
current = Avx2.PermuteVar8x32(current,
    LoadDquVector256(PermTablePtr + mask * 8));
Avx.Store(writeLeft, current);
Avx.Store(writeRight, current);
var popCount = PopCnt.PopCount(mask);
writeRight -= popCount;
writeLeft  += 8 - popCount;
```

That's a lot of cheese, let’s break this down:

* In line 1, we’re broadcasting the pivot value to a vectorized register I’ve named `P`.  
  ````csharp
  var P = Vector256.Create(pivot); 
  ````
  We’re just creating 8-copies of the selected pivot value in our `P` value/register. It is important to remember that this happens only *once* in the entire partitioning function call!
  
* Next in line 3:  
  ```
  var current = Avx2.LoadDquVector256(nextPtr);
  ```
  We load up the data from somewhere (`nextPtr`) in our array. We’ll focus on where `nextPtr` points to later, but for now we can go forward, we have data we need to partition.

* Then comes an 8-way comparison using `CompareGreaterThan` & `MoveMask` call in lines 4-5:  
  ```csharp
  var mask = (uint) Avx.MoveMask(
    Avx2.CompareGreaterThan(current, P).AsSingle()));
  ```
  This ultimately generates a **scalar** `mask` value which will contain `1` bits for every comparison where the respective data element was greater-than the pivot value, and `0` bits for all other elements. If you are having a hard time following *why* this does this, you need to head back to the [2<sup>nd</sup> post](2019-08-08-trumping-arraysort-with-avx2-pt2.md) and read up on these two intrinsics / watch the respective animations…

* In lines 6-7 we permute the loaded data according to a permutation value:  
  
  ````csharp
  current = Avx2.PermuteVar8x32(current,
      LoadDquVector256(PermTablePtr + mask * 8));
  ````
  
  Here comes a small surprise! We’re going to use the `mask` as an index into a lookup-table for permutation values! Bet you didn't see that one coming...  
  This is one reason it was super critical for us to have the `MoveMask` intrinsic in the first place! Without it we wouldn’t have `mask` as a scalar value/register, and wouldn’t be able to use it as an offset to our table. Pretty neat, no?    
  With this permutation operation done, we’re grouping all the smaller-or-equal than values on one side of our `current` SIMD vector/register (let’s call it the left side) and all the greater than values on the other side (which we’ll call the right side).  
  I’ve comfortably glanced over the actual values in the permutation lookup-table which `PermTablePtr` is pointing to, worry not, it’s just a couple of paragraphs down.
  
* In case this wasn’t abundantly clear, the entire partitioning operation is now complete. That is, our `current` SIMD value/register is already partitioned by line 8, we’re practically done, except that we need to write the partitioned values back to memory. Here comes a small complication: Our `current` value now contains both values that are smaller-or-equal than the pivot and larger. The permutation operation did group them together on both "sides" of the register, but we need this to be reflected all the way to our memory/array.  
  What I ended up doing was to write the **entire** partitioned vector to both the *left* **and** *right* sides of the array!  
  At any given moment, we have two write pointers pointing to where we need to write stuff to on either side: `writeLeft` and `writeRight`. Again, how those are initialized will be dealt with further down this post where we discuss the outer-loop, but for now lets assume these pointers initially point to somewhere where it’s safe to write a single entire 256 bit SIMD register, and move on. In lines 8,9 we just store the entire partition SIMD register to **both** sides in two calls:
  
  ```csharp
  Avx.Store(writeLeft, current);
  Avx.Store(writeRight, current);
  ```
  
* We just wrote 8 elements to each side, but in reality the partitioned register had a mix of values: some were destined to the left side of the array, and some to the right. We didn't care for it while we were writing, but we need to make sure the next write pointers are adjusted according to how the values were partitioned inside the register…  
  Luckily, we have the `PopCount` intrinsic to lend us a hand here on line 10, we `PopCount` the mask value (again, `MoveMask` was worth its weight in gold here) and get a count of how many bits in the mask value were `1`. Remember that this count directly corresponds to how many values **inside** the SIMD register were greater-than the pivot value, which is exactly the amount by which we want to decrease the `writeRight` pointer:
  
  ```csharp
  var popCount = PopCnt.PopCount(mask);
  writeRight -= popCount;
  ```
  
  Which is exactly what we do in line 11, note that the `writeRight` pointer is "advanced" by decrementing it!
  
* And finally, since we know that there were exactly 8 elements, and the `popCount` value contains the number of `1` bits, the number of `0` bits is `8 - popCount` since `mask` only had 8 bits to data in it , which is really the count of how many values in the register where *less-than-or-equal* the pivot value. So again we advance the `writeLeft` pointer:

  ```csharp
  writeLeft  += 8 - popCount;
  ```

And we’re done!

This was a full 8-element wise partitioning block, and it's worth noting a thing or two about it:

* The entire thing is completely branch-less, we've given the CPU a nicely sized loop body with no need to speculate on what code gets executed. It sure looks pretty when you consider the amount of branches our scalar code would do for the same amount of work. Don't celebrate yet though, we're about to run into a wall in a second, but sure feels good for now.
* The permutation-table seems magical, and there are some tricks we can still pull around it. Try to think what's not so great about before I address this down below.
* The only dependency between different iterations is the mutation of the `writeLeft` and `writeRight` pointers. This is the only dependency we "carry" between different iterations inside the CPU as it's executing our code, it's unavoidable (well, I couldn't, maybe you can), but worth while mentioning nonetheless. If you need a reminder about how dependencies can change the dynamics of efficient execution you can read up when I tried my best to go at it battling with [`PopCount` to run screaming fast](2018-08-20-netcoreapp3.0-intrinsics-in-real-life-pt3.md)

I thought it would be nice to show off that the JIT is well behaved in this case with the generated x64 asm:

```nasm
vmovd xmm1,r15d                      ; Broadcast
vbroadcastd ymm1,xmm1                ; pivot
...
vlddqu ymm0, ymmword ptr [rax]       ; load 8 elements
vpcmpgtd ymm2, ymm0, ymm1            ; compare
vmovmskps ecx, ymm2                  ; movemask into scalar reg
mov r9d, ecx                         ; copy to r9
shl r9d, 0x3                         ; *= 8
vlddqu ymm2, qword ptr [rdx+r9d*4]   ; load permutation
vpermd ymm0, ymm2, ymm0              ; permute
vmovdqu ymmword ptr [r12], ymm0      ; store left
vmovdqu ymmword ptr [r8], ymm0       ; store right
popcnt ecx, ecx                      ; popcnt
shl ecx, 0x2                         ; pointer
mov r9d, ecx                         ; arithmetic
neg r9d                              ; for += 8 - popCount
add r9d, 0x20                        ;
add r12, r9                          ; Update writeLeft pos
sub r8, rcx                          ; Update writeRight pos

```
I think anyone who followed the C# code can use the intrinsics table from the previous post and really read this assembly code without further help. If that's not a sign that the JIT is literally taking our intrinsics straight to the CPU as is, I don't know what is!

I've tried to optimize the block further with various tricks, but so far nothing achieved anything I would consider substantial of even measurable improvement, things I tried:

* Detecting the cases where no permutation needs to happen (all `1` / `0` bits in the mask are already consecutive and in place) and skipping the permutation all-together. (Didn't really run faster, can't tell why).
* Re-organizing the instructions so that the `PopCount`ing happens sooner (The CPU already probably does this without my generous help)

## Permutation lookup table

The permutation lookup table is the elephant in the room at this stage, so let's see what's in them:

* The table needs to have 2<sup>8</sup> elements in it
* Each element ultimately needs to be a `Vector256<int>` because that's what Intel expects from us, so 8 x 4 bytes = 32 bytes per element.
  * That's 8kb of lookup data in total (!)
* The values inside need are pre-generated so that they just shuffle the data inside the vector in such a way that all values that got a corresponding `1` bit in the mask go to one side, and the elements with a `0` go to the other side. There's no particular order amongst the grouped elements since we're partitioning around a pivot value, nothing more, nothing less.

Here are 4 sample values from the permutation table that I've copy-pasted so we can get a feel for it:

```csharp
static ReadOnlySpan<int> PermTable => new[] {
    0, 1, 2, 3, 4, 5, 6, 7,     // 0   => 0b00000000
    // ...
    3, 4, 5, 6, 7, 0, 1, 2,     // 7   => 0b00000111
    // ...
    0, 2, 4, 6, 1, 3, 5, 7,     // 170 => 0b10101010
    // ...
    0, 1, 2, 3, 4, 5, 6, 7,     // 255 => 0b11111111
};
```

* For `mask` values 0, 255 are simple to grok: if everything was `1` or `0` in the `mask`, there's nothing we need to do with the data, so just leave the data as is, the permutation vector: `[0, 1, 2, 3, 4, 5, 6, 7]` achieves just that.
* When `mask == 7`, the 3 lowest (first) bits of the `mask` are `1`, but those need to go to the right side of the vector (`> pivot`), while all other values need to go to the left (`<= pivot`). The permutation vector: `[3, 4, 5, 6, 7, 0, 1, 2]` does just that.
* The checkered bit patten for the `mask` value `0b10101010` (170) calls to move all the even elements to one side and the odd elements to the other... You can see that `[0, 2, 4, 6, 1, 3, 5, 7]` does the work here.

If you look at the actual code, you'll see that the values inside the permutation table in the code are actually coded as a `ReadOnlySpan<byte>`. This is a CoreCLR specific optimization that allows us to tread the address of this table as a constant at JIT time. Kevin Jones did a wonderful job of digging into it, go [read his excellent post](https://vcsjones.dev/2019/02/01/csharp-readonly-span-bytes-static/) about this.
{: .notice--info}

It's important to note that this HAS to be a `ReadOnlySpan<byte>` for the optimization to trigger (that was two nights of my life chasing what I was sure had to be a GC / JIT bug). Normally, it would really be a bad idea to store a `ReadOnlySpan<int>` as a `ReadOnlySpan<byte>` in C#, since we have to choose big/little endian encoding and our actual CPU might not use the same encoding as we chose during compilation time... But luckily this is a non-issue, as this entire code is Intel specific, and we can simply assume little endianess here till the end of times.
{: .notice--warning}

This is the basic layout of the permutation table, but we can do better!

### Packing the Permutation Table

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

What does this little monstrosity do exactly? We pack the permutation bits (remember, we just need 3 bits per element, we have 8 elements, so 24 bits per permutation vector in total) into a single 32 bit value, then uses, the `BMI2` `ParallelBitDeposit`, which again, in a stroke of luck I've already throughly explained back in my `PopCount` series [here](2018-08-19-netcoreapp3.0-instrinsics-in-real-life-pt2.md). I won't go deep into it, but this promoted that packed data into a 64-bit wide element, which we then convert to a 128-bit SIMD register using `Vector128.CreateScalarUnsafe` and then we use yet another intrinsic [`ConvertToVector256Int32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_cvtepi8_epi32&expand=1532) that was just waiting for us to call it, since it can take 8-bit elements from a 128-bit wide register and expand them into integers in a 256 bit registers.

Is it worth it? I wish I could say with a complete and assured voice that it does, but the truth is that it had only marginal benefits. While we end up using 256 bytes of cache instead of 8KB, the extra instructions still cost us quite a lot more. I did see this push perf up by 1%-2% compared to 8KB lookup tables, but I'm not sure it's such a big win. It might not be worth it, or maybe I still need to figure out how/why it can be. Time will tell.

## Outer-Loop for in-place sorting





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

