---
title: "Trumping Array.Sort with AVX2 Intrinsics (Part 2/6)"
header:
  image: /assets/images/coreclr-clion-header.jpg
hidden: true
date: 2019-08-19 08:26:28 +0300
classes: wide
#categories: coreclr intrinsics vectorization quicksort sorting
---

I ended up going down the rabbit hole re-implementing `Array.Sort()` with AVX2 intrinsics, and there’s no reason I should store all the agony inside (to be honest: I had a lot of fun with this). I should probably attempt to have a serious discussion with CoreCLR  / CoreFX people about starting a slow process that would end with integrating this code into the main C# repos, but for now, let's get in the ring and show what AVX/AVX2 intrinsics can really do for a non-trivial problem, and even discuss potential improvements that future CoreCLR versions could bring to the table.

Since there’s a lot to over go over here, I’ll split it up into a few parts:

1. In [part 1](2019-08-08-trumping-arraysort-with-avx2-pt1.md), we did a short refresher on `QuickSort` and how it compares to `Array.Sort()`. If you don’t need any refresher, you can skip over it and get right down to part 2 and onwards , although I really recommend skimming through, mostly because I’ve got really good visualizations for that should be in the back of everyone’s mind as we’ll be dealing with vectorization & optimization later.
2. In this part, we go over the basics of Vectorized HW Intrinsics, discussed vector types, and a handful of vectorized instructions we’ll actually be using in part 3, but we still weren't sorting anything.
3. In [part 3](2019-08-08-trumping-arraysort-with-avx2-pt3.md) we go through the initial code for the vectorized sorting and we’ll finally start seeing some payoff. We’ll finish with some agony courtesy of CPU’s Branch Predictor, just so we don't get too cocky.
4. In [part 4](2019-08-08-trumping-arraysort-with-avx2-pt4.md), we go over a handful of optimization approaches that I attempted trying to get the vectorized partition to run faster, we'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of 100% of the remaining scalar code, by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization and gain a considerable amount of performance / efficiency in the process.
6. Finally, in part 6, I’ll list the outstanding stuff / ideas I have for getting more juice and functionality out of my vectorized code.

## Vectorization

To really understand what intrinsics are all about here are some numbers that should help with getting a proper direction. I gathered them by doing horrible unix scripting around Intel's own [data-3.4.6.xml](https://software.intel.com/sites/landingpage/IntrinsicsGuide/files/data-3.4.6.xml).

This XML file is part of the [Intel Intrinsics Guide](https://software.intel.com/sites/landingpage/IntrinsicsGuide/), an invaluable resource on intrinsics by-itself, and really the "database" behind the guide, which I'll refer to later. What I learned was that:

* There are no less than 1,218 intrinsics (!)
  * Those can be combined in 6,180 different ways (according to operand sizes and types).
  * They're grouped into 67 different categories / groups.
* More that 90% of them use vectorized types / registers

That last point is super critical: CPU intrinsics, at least in 2019, are *mostly* about being able to execute vectorized instructions using vectorized registers, on a modern CPU. That's really *why* you should be paying them attention in the first place. There is additional stuff in there, specifically if your a kernel developer, or doing crypto code, and some other niche-cases, but vectorization is why you are really here, whether you knew it or not.

So what are these vectorized intrinsics?

### SIMD What & Why

I'm going to use Vectorization and SIMD interchangeably from hereon, but for the first and last time, let's just define exactly what SIMD is: **S**ingle **I**nstruction **M**ultiple **D**ata is really a simple idea if you think about it...

A lot of code ends up doing "stuff" in loops, normally processing vectors of data one element at a time. SIMD instructions bring a simple new idea to the table: The CPU adds special instructions that can do arithmetic, logic and many other types of generalized operations on "vectors".

One benefit of using this approach to computing, both from the perspective of CPU designers and from that of coders is that going down this path allows for greater efficiency: If we end up using roughly the same number of instructions in our code-stream for scalar/vectorized code but process, let’s say, 8 elements every instruction, we will naturally be running less instructions while the CPU does more work per instruction, this optimizes the CPUs time in the sense that there are fewer instruction to *decode* for the same amount of work. And instruction decoding is one of the most demanding tasks a CPU has to do, so that’s at least one very clear win.

Another, although much less obvious, benefit is, the fact that most vectorized operation are actually rather cheap to implement in silicon, when compared to the other alternatives! or in other words, in the land of CPU feature economics, vectorization is high bang-for-buck feature: you can get a lot of performance for relatively little work, as long as people adapt their code (e.g. rewrite it) to use intrinsics, or compilers somehow magically pick up the magical ability of auto-vectorizing the code.

Of course I’m grossly over simplifying the process and there are many hidden overheads that have to do with adding more instructions to decode, and some vectorized instructions are very tricky to implement, but all in all, the grand picture of CPU economics remains the same, adding vectorized instructions is still, relatively to other things, quite cheap, and to complete my circular argument, this is how and why over 90% of CPU intrinsics have to do with vectorized code.

#### SIMD registers

So, with some background on vectorized instructions out of our way, we need to talk about SIMD registers.  Teaching our CPU to execute 1,000+ vectorized instructions is just part of the story. These instructions need to operate on our data somehow. Do all of these instructions simply take a pointer into memory and go wild with it? The short answer to that is: **No**.  
For the *most* part, CPU instructions dealing with vectorization use special registers inside our CPU that are called SIMD registers. This is pretty analogous to scalar (“normal”) code we write in any programming language: While there are obviously instructions that read and write to memory, and an occasional instruction that can directly accept a memory address as an operand, many instructions are register ↔ register *only*.

For now, this is all I care to explain about SIMD Registers at the CPU level: just like you don't necessarily need to know how many scalar registers exist and the details surrounding how the JIT generates code to use them efficiently for a given CPU, you can skip attaining that knowledge when starting out with vectorization as well. We will probably circle back later and give more attention to these details when, invariably, our vectorized code doesn't perform as quickly as we'd like it to, but for now we just need the basics.

To work with SIMD instructions in C# / .NET we need to get acquainted with the primitive vector types that the JIT recognizes and knows how to work with, In C# those would be:

* [`Vector64<T>`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector64?view=netcore-3.0)
* [`Vector128<T>`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector128?view=netcore-3.0)
* [`Vector256<T>`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector256?view=netcore-3.0)

These are special value-types recognized by the JIT while it is busy generating machine code. We should think about these types just like we think about other special-case primitive types such as `int` or `double` except for one additional complication: They are generic!  
As you can obviously tell, these types all accept a generic parameter `<T>`. This parameter can’t just be anything you'd like. It is limited to types to are actually supported on our CPU.

In addition, the *nnn* number as part of the `Vector`*nnn*`<T>` name denotes the number of bits in the vectorized type.
As we've mentioned before, since everything gets translated eventually to CPU registers, and CPU registers are obviously of constant size (remember they’re really hardware), this isn't just some number the CLR developers pulled out of thin air! These are actually the register widths supported by various CPUs, in bits.

Let's take `Vector256<T>`, which I'll be using extensively in this series anyway, as an example; `Vector256<T>` can be used with the following primitive types:

<table class="fragment">
<thead><th style="border: none"><code>typeof(T)</code></th>
<th/>
<th style="border: none"><code># elements</code></th>
<th style="border: none"></th>
<th style="border: none"><code>element width (bits)</code></th>
</thead>
<tbody>
<tr><td style="border: none"><code>byte / sbyte</code></td>  <td style="border: none">➡</td><td style="border: none">32</td><td style="border: none">x</td><td style="border: none">8b</td></tr>
<tr><td style="border: none"><code>short / ushort</code></td><td style="border: none">➡</td> <td style="border: none">16</td><td style="border: none">x</td><td style="border: none">16b</td></tr>
<tr><td style="border: none"><code>int / uint</code></td>    <td style="border: none">➡</td> <td style="border: none">8</td><td style="border: none">x</td><td style="border: none">32b</td></tr>
<tr><td style="border: none"><code>long / ulong</code></td>  <td style="border: none">➡</td> <td style="border: none">4</td><td style="border: none">x</td><td style="border: none">64b</td></tr>
<tr><td style="border: none"><code>float</code></td><td style="border: none">➡</td> <td style="border: none">8</td><td style="border: none">x</td><td style="border: none">32b</td></tr>
<tr><td style="border: none"><code>double</code></td> <td style="border: none">➡</td> <td style="border: none">4</td><td style="border: none">x</td><td style="border: none">64b</td></tr>
    </tbody>
</table>

It's easy to see that no matter what type of the supported types we'll choose, we'll obviously end up with 256 bits in total, or the underlying CPUs register width behind this special value-type.

#### A few Vectorized Instructions for the road

Armed with this new understanding and knowledge of `Vector256<T>` (again, artificially limiting myself here to `Vector256<T>`) we can move on and start learning a few vectorized instructions.

I'm obviously not going to explain too many of 1,000+ intrinsics I mentioned, but since I already know how my code ends up looking like by the end of this series I will **thoroughly** explain the ones I know I need to get this party going.

Here's the list of what we're going to go over:

<table><thead><tr>
<th style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>x64 asm</code></span></th>
<th style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>Intel</code></span></th>
<th style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>CoreCLR</code></span></th>
</tr></thead>
<tbody><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>vbroadcastd</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm256_broadcastd_epi32</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Vector256.Create(int)</code></span></td>
</tr><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>vlddqu</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm256_lddqu_si256</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Avx.LoadDquVector256</code></span></td>
</tr><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>vmovdqu</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm256_storeu_si256</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Avx.Store</code></span></td>
</tr><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>vpcmpgtd</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm256_cmpgt_epi32</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Avx2.CompareGreaterThan</code></span></td>
</tr><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>vmovmskps</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm256_movemask_ps</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Avx.MoveMask</code></span></td>
</tr><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>popcnt</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm_popcnt_u32</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Popcnt.PopCount</code></span></td>
</tr><tr>
<td style="text-align:left;"  ><span class="fragment" data-fragment-index="3"><code>vpermd</code></span></td>
<td style="text-align:center;"><span class="fragment" data-fragment-index="2"><code>_mm256_permutevar8x32_epi32</code></span></td>
<td style="text-align:right;" ><span class="fragment" data-fragment-index="1"><code>Avx2.PermuteVar8x32</code></span></td>
</tr></tbody></table>

I understand that for first time readers, this list looks like I'm just name-dropping tons of code names to make myself sound smart, but the unfortunate reality is that we *kind of need* to know all of these, and here is why:  
On the right column I've provided the actual C# Intrinsic name we will be calling in our code. But here's a funny thing: there is no "usable" documentation on Microsoft's own docs regarding these intrinsics(!). In what I can only describe as an un-microsofty way this what the docs for [`Avx2.CompareGreaterThan()`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx2.comparegreaterthan?view=netcore-3.0#System_Runtime_Intrinsics_X86_Avx2_CompareGreaterThan_System_Runtime_Intrinsics_Vector256_System_Int32__System_Runtime_Intrinsics_Vector256_System_Int32__) look like for example (click the link if you don't believe me):

> __m256i _mm256_cmpgt_epi32 (__m256i a, __m256i b) VPCMPGTD ymm, ymm, ymm/m256

That's it! That really is 100% of the docs provided by the CLR.  
Coincidentally, this also explains why we need to keep the intel name in the back of our minds at all times: Any real documentation will be acquired by going to resources like the [Intel Intrinsics Guide](https://software.intel.com/sites/landingpage/IntrinsicsGuide/) I’ve mentioned before and searching using the intel name to get something more helpful, like this (again for the same intrinsic):

<iframe src="../talks/intrinsics-dotnetos-2019/intrinsics-guide/vpcmpgt.html" frameborder="0" width="1600" height="350" marginwidth="0" marginheight="0" scrolling="" style="border:3px solid #666; max-width: 100%;background: #FFFFFF;" allowfullscreen=""></iframe>  
`<rant>`  
Now, If I was in my annoyed state of mind, I'd bother to mention that [I always thought](https://github.com/dotnet/corefx/issues/2209#issuecomment-317124449) that introducing 200+ functions with already established names (in C/C++/rust) and forcing everyone to learn new names whose only saving grace is that they look BCL*ish* to begin with was not the friendliest move on Microsoft's part, and that trying to give C# names to the utter mess that Intel created in the first place was a thankless effort to begin with and would only annoy everyone more, and would eventually run up against the inhumane names Intel went for (Yes, I'm looking at you `LoadDquVector256`, you are not looking very BCL-ish to me with the `Dqu` slapped in the middle there : (╯°□°)╯︵ ┻━┻)...  
But thankfully, I'm not in my annoyed state.  
`</rant>`
{: .notice--warning}

In short, we need to be aware of the Intel names anyway, and since we're practically writing assembly code anyways, I can guarantee we'll end up inspecting JIT'd code, so we might as well as learn the x64 assembly names for our instructions as well.  
Now, What does each of these do? Let's find out...

As luck would have it, I was blessed with the ultimate power of wielding svg animations, so most of these instruction will be accompanied by an animation visually explaining them. 
*Hint*: These animations are triggered by your mouse pointer  / finger touch inside them. The animations will immediately stop/freeze once the pointer is out of the drawing area, and resume when inside...  
From hereon, I'll use the following icon when I have a thingy that animates:<br/><object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/play.svg"></object>
{: .notice--info}

#### Vector256.Create(int value)

We’ll start with a couple of simple instructions, and nothing is more simple than this first one.

This intrinsic accepts a single scalar value and simply “broadcasts” it to an entire SIMD register, this is how you’d use it:

```csharp
Vector256<int> someVector256 = Vector256.Create(0x42);
```

This will load up `someVector256` with 8 copied of `0x42` once executed, and in x64 assembly, the JIT will produce something quite simple:

```nasm
vmovd  xmm0, rax          ; 3 cycle latency / 1 cycle throughput
vpbroadcastd ymm0, xmm0   ; 3 cycle latency / 1 cycle throughput
```

This is a slightly challenging disassembly, since there is no direct single operation that performs this, the JIT generates *two* instructions, one to load the scalar register (`rax` in this case) into a 128-bit wide register (`xmm0`), and a second instruction to broadcast the lower 32 bits in `xmm0` into the 256-bit `ymm0` register.  
It sounds like a lot when expressed in English. But very simple in reality: 

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/inst-animations/vbroadcast-with-hint.svg"></object>
#### LoadDquVector256 / Store

Another couple of brain dead simple HW intrinsics are the ones we use to read / write from memory into SIMD registers, and conversely store from SIMD registers back to memory. Simple as these may be, they are obviously amongst common intrinsics out there:

```csharp
int *ptr = ...; // Get some pointer to a big enough array

Vector256<int> data = Avx.LoadDquVector256(ptr);
...
Avx.Store(ptr, data);
```

And in x64 assembly:

```nasm
vlddqu ymm1, ymmword ptr [rdi]  ; 5 cycle latency + cache/memory
                                ; 0.5 cycle throughput
vmovdqu ymmword ptr [rdi], ymm1 ; Same as above
```

I only included an SVG animation for `LoadDquVector256`, but you can use your imagination and see that `Store` simply does the same thing in opposite:

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/inst-animations/lddqu.svg" ></object>
#### CompareGreaterThan

`CompareGreaterThan` does an *n*-way, elemet-by-element *greater-than* (`>`) comparison between the elements of a `Vector256<T>`. In our case where `T` is really int, this means an 8-element comparison!

But how to we get the result? In a new `Vector256<int>` of course! The result vector contains 8 comparison results, where in each position where the element in the first vector was *greater-than* the element in the second vector we get a `-1` value for that result element, or `0` for all other cases.  
Calling this is rather simple:

```csharp
Vector256<int> data, comperand;
Vector256<int> result =
    Avx2.CompareGreaterThan(data, comperand);
```

And in x64 assembly, this is pretty simple too:

```nasm
vpcmpgtd ymm2, ymm1, ymm0 ; 1 cycle latency
                          ; 0.5 cycle throughput
```

Visually this does:

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/inst-animations/vpcmpgtd.svg" ></object>
#### MoveMask

A very useful thing to have is the ability to extract some bits from a vectorized register into a normal, scalar one.  
`MoveMask` does just this!, this intrinsic, takes the top-level (MSB) bit from every element and moves it into our scalar result:

```csharp
Vector256<int> data;
int result = Avx.MoveMask(data.AsSingle());
```

There’s an oddity here, as you can tell by that awkward `.AsSingle()` call. While this looks like we’re really doing “something” with our `Vector256<int>` and somehow casting it do single-precision floating point values, let me assure you, this is just smoke and mirrors: The intrinsic simply accepts only floating point values (32/64 bit ones), so we have to “cast” the data to `Vector256<float>`, or alternatively call `.AsSingle()` before calling `MoveMask`. Yes, this is super awkward from a pure C# perspective, but in reality, the JIT understands these shenanigans and really ignores them completely, The assembly instruction here is exactly as simple as you would think:

```nasm
vmovmskps rax, ymm2  ; 5 cycle latency
                     ; 1 cycle throughput
```

I have no idea why the CLR developers chose not to provide “fake” signatures that would accept additional types like `Vector256<int>`, `Vector256<uint>` , `Vector256<long>` and `Vector256<ulong>`, to avoid the awkwardness, but they were pretty consistent in doing a 1:1 mapping to Intel intrinsics, and did not venture too much off the exact Intel way of things. Except for the naming (sorry, can’t let that one go)
{: .notice--info}

<object stle="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/inst-animations/vmovmskps.svg"></object>
#### PopCount

`PopCount` is really a very simple, useful intrinsic, which [I've covered extensively before](https://bits.houmus.org/2018-08-18/netcoreapp3.0-instrinsics-in-real-life-pt1): `PopCount` returns the number of `1` bits in a 32/64 bit primitive.  
In C#, we would use it as follows::

```csharp
int result = PopCnt.PopCount(0b0000111100110011);
// result == 8
```
And in x64 assembly code:

```nasm
popcnt rax, rdx  ; 3 cycle latency
                 ; 1 cycle throughput
```
I didn't bother to do a graphical animation for `PopCount` as it really is super simple to understand.

By the way, although this intrinsic doesn't accept nor return one of the SIMD registers / types, you could consider it to be a non-vectorized intrinsic as far as classification goes, but as far as I'm concerned bit-level intrinsic functions that operate on scalar registers are just as "vectorized" as their "pure" vectorized sisters, as they mostly deal with scalar values as vectors of bits  
{: .notice--info}

#### PermuteVar8x32

`PermuteVar8x32` accepts two vectors: source, permutation and performs a permutation operation **on** the source value *according to the order provided* in the permutation value! 

```csharp
Vector256<int> data, perm;
Vector256<int> result = Avx2.PermuteVar8x32(data, perm);
```
While technically speaking, both the `data` and `perm` parameters are of type `Vector256<int>` and can contain any integer value in their elements, only the 3 least significant bits in `perm` are taken into account for permutation of the elements in `data`.  
This should make sense, as we are permuting a 8-element vector, so we need 3 bits (2<sup>3</sup> == 8) in every permutation element to figure out which element goes where... In x64 assembly this is:

```nasm
vpermd ymm1, ymm2, ymm1 ; 3 cycles latency
                        ; 1 cycles throughput
```
And with our trusty animations, what goes on under the hood becomes that much clearer:
<object stle="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/inst-animations/vpermd.svg"></object>
#### That’s it for now

This post was all about laying the ground work before this whole mess comes together.  
Remember, we’re re-implementing QuickSort here, which for the most part, means re-implementing the partitioning function from our scalar code in the previous post with AVX2 intrinsics.  
I’m sure there are wheels turning in many heads now as you are trying to figure out what comes next…  
I think it might be a good time as any to end this post, and leave you with a suggestion: Try to take a piece of paper or your favorite text editor, and see if you can some cobble up these instructions into something that can partition numbers given a selected pivot.

When you’re ready, head on to the [next post](2019-08-08-trumping-arraysort-with-avx2-pt3.md) to see how the whole thing comes together, and how fast we can get it to run with a basic version…
