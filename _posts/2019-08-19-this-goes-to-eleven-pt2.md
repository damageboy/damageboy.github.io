---
title: "This Goes to Eleven (Pt. 2/6)"
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
date: 2019-08-19 08:26:28 +0300
classes: wide
#categories: coreclr intrinsics vectorization quicksort sorting
---

Since there’s a lot to over go over here, I’ve split it up into no less than 6 parts:

1. In [part 1]({% post_url 2019-08-18-this-goes-to-eleven-pt1 %}), we start with a refresher on `QuickSort` and how it compares to `Array.Sort()`.
2. In this part, we go over the basics of vectorized hardware intrinsics, vector types, and go over a handful of vectorized instructions we’ll use in part 3. We still won't be sorting anything.
3. In [part 3]({% post_url 2019-08-20-this-goes-to-eleven-pt3 %}), we go through the initial code for the vectorized sorting, and we’ll start seeing some payoff. We finish agonizing courtesy of the CPU’s Branch Predictor, throwing a wrench into our attempts.
4. In [part 4]({% post_url 2019-08-21-this-goes-to-eleven-pt4 %}), we go over a handful of optimization approaches that I attempted trying to get the vectorized partitioning to run faster. We'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of all the remaining scalar code- by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization.
6. Finally, in part 6, I’ll list the outstanding stuff/ideas I have for getting more juice and functionality out of my vectorized code.

## Intrinsics / Vectorization

I'll start by repeating my own words from the first [blog post where I discussed intrinsics]({% post_url 2018-08-18-netcoreapp3.0-intrinsics-in-real-life-pt1 %}#the-whatwhy-of-intrinsics) in the CoreCLR 3.0 alpha days:

> Processor intrinsics are a way to directly embed specific CPU instructions via special, fake method calls that the JIT replaces at code-generation time. Many of these instructions are considered exotic, and normal language syntax does cannot map them cleanly.  
> The general rule is that a single intrinsic "function" becomes a single CPU instruction.

You can go and re-read that introduction if you care for a more general and gentle introduction to processor intrinsics. For this series, we are going to focus on vectorized intrinsics in Intel processors. This is the largest group of CPU specific intrinsics in our processors, and I want to start by showing this by the numbers. I gathered some statistics by processing Intel's own [data-3.4.6.xml](https://software.intel.com/sites/landingpage/IntrinsicsGuide/files/data-3.4.6.xml). This XML file is part of the [Intel Intrinsics Guide](https://software.intel.com/sites/landingpage/IntrinsicsGuide/), an invaluable resource on intrinsics in itself, and the "database" behind the guide. What I learned was that:

* There are no less than 1,218 intrinsics in Intel processors[^0]!
  * Those can be combined in 6,180 different ways (according to operand sizes and types).
  * They're grouped into 67 different categories/groups, these groups loosely correspond to various generations of CPUs as more and more intrinsics were gradually added.
* More than 94% are vectorized hardware intrinsics, which we'll define more concretely below.

That last point is supercritical: CPU intrinsics, at least in 2019, are overwhelmingly about being able to execute vectorized instructions. That's really why you *should* be paying them attention in the first place. There is additional stuff in there, for sure: if you're a kernel developer, or writing crypto code, or some other niche-cases, but vectorization is why you are really here, whether you knew it or not.

In C#, we've mostly shied away from having intrinsics until CoreCLR 3.0 came along, where intrinsic support was added, championed by [@tannergooding](https://twitter.com/tannergooding) and others from Microsoft (Thanks Tanner!). but as single-threaded performance has virtually stopped improving, more programming languages started adding intrinsics support (go, rust, Java and now C#) so developers in those languages would have access to these specialized, much more efficient instructions. CoreCLR 3.0 does not support all 1,218 intrinsics that I found, but a more modest 226 intrinsics in [15 different classes](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86?view=netcore-3.0&viewFallbackFrom=dotnet-plat-ext-3.0) for x86 Intel and AMD processors. Each class is filled with many static functions, all of which are processor intrinsics, and represent a 1:1 mapping to Intel group/code names. As C# developers, we roughly get access to everything that Intel incorporated in their processors manufactured from 2014 and onwards[^1], and for AMD processors, from 2015 onwards.

What are these vectorized intrinsics?  
We need to cover a few base concepts specific to that category of intrinsics before we can start explaining specific intrinsics/instructions:

* What are vectorized intrinsics, and why have they become so popular.
* How vectorized intrinsics interact with specialized vectorized *registers*.
* How those registers are reflected as, essentially, new primitive types in CoreCLR 3.0.

### SIMD What & Why

I'm going to use vectorization and SIMD interchangeably from here-on, but for the first and last time, let's spell out what SIMD is: **S**ingle **I**nstruction **M**ultiple **D**ata is really a simple idea when you think about it. A lot of code ends up doing "stuff" in loops, usually, processing vectors of data one element at a time. SIMD instructions bring a simple new idea to the table: The CPU adds special instructions that can do arithmetic, logic and many other types of generalized operations on "vectors", e.g. process multiple elements per instruction.

The benefit of using this approach to computing is that it allows for much greater efficiency: When we use vectorized intrinsics we end up executing the same *number* of instructions to process, for example, 8 data elements per instruction. Therefore, we reduce the amount of time the CPU spends decoding instructions for the same amount of work; furthermore, most vectorized instructions operate *independently* on the various **elements** of the vector and complete their operation at the same number of CPU cycles as the equivalent non-vectorized (or scalar) instruction. In short, in the land of CPU feature economics, vectorization is considered a high bang-for-buck feature: You can get a lot of *potential* performance for relatively little transistors added to the CPU, as long as people are willing to adapt their code (e.g. rewrite it) to use your new intrinsics, or compilers somehow magically manage to auto-vectorize the code (hint: There are tons of problems with that too)[^2].

Another equally important thing to embrace and understand about vectorized intrinsics is what they don’t and cannot provide: branching. It’s pretty much impossible to even attempt to imagine what a vectorized branch instruction would mean. Those two concepts just don’t even mix. Appropriately, a substantial part of vectorizing code is forcing oneself to accomplishing the given task without using branching. As we will see, branching begets unpredictability, at the CPU level, and unpredictability is our enemy, when we want to reach the stratosphere, performance wise!

Of course, I’m grossly over-romanticizing vectorized intrinsics and their benefits: There are also many non-trivial overheads involved both using them and adding them to our processors. But all in all, the grand picture of CPU economics remains the same, adding and using vectorized instructions is still, compared to other potential improvements, quite cheap, under the assumption that programmers are willing to make the effort to re-write and maintain vectorized code.

#### SIMD registers

After our short introduction to vectorized intrinsics, we need to discuss SIMD registers, and how this piece of the puzzle fits the grand picture: Teaching our CPU to execute 1,000+ vectorized instructions is just part of the story, these instructions need to somehow operate on our data. Do all of these instructions simply take a pointer to memory and run wild with it? The short answer is: **No**. For the *most* part, CPU instructions dealing with vectorization (with a few notable exceptions) use special registers inside our CPU that are called SIMD registers. This is analogous to scalar (regular, non-vectorized) code we write in any programming language: while some instructions read and write directly to memory, and occasionally some instruction will accept a memory address as an operand, most instructions are register ↔ register only.

Just like scalar CPU registers, SIMD registers have a constant bit-width. In Intel these come at 64, 128, 256 and recently 512 bit wide registers. Unlike scalar registers, though, SIMD registers, end up *containing multiple* data-elements of another primitive type. The same register can and will be used to process a wide-range of primitive data-types, depending on which instruction is using it, as we will shortly demonstrate.

For now, this is all I care to explain about SIMD Registers at the CPU level: We need to be aware of their existence (we'll see them in disassembly dumps anywat), and since we are dealing with high-perfomance code we kind of need to know how many of them exist inside our CPU.

#### SIMD Intrinsic Types in C\\#

We've touched lightly upon SIMD intrinsics and how they operate (e.g. accept and modify) on SIMD registers. Time to figure out how we can fiddle with everything in C#; we'll start with the types:

| C# Type                                                      | x86 Registers    | Width (bits) |
| ------------------------------------------------------------ |:------------:|:------------:|
| [`Vector64<T>`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector64?view=netcore-3.0) | `mmo-mm7`    | 64  |
| [`Vector128<T>`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector128?view=netcore-3.0) | `xmm0-xmm15` | 128 |
| [`Vector256<T>`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector256?view=netcore-3.0) | `ymm0-ymm15` | 256 |

These are primitive vector value-types recognized by the JIT while it is generating machine code. We should try and think about these types just like we think about other special-case primitive types such as `int` or `double`, with one exception: These vector types all accept a generic parameter `<T>`, which may seem a little odd for a primitive type at a first glance, until we remember that their purpose is to contain *other* primitive types (there's a reason they put the word "Vector" in there...); moreover, this generic parameter can’t just be any type or even value-type we'd like... It is limited to the types supported on our CPU and its vectorized intrinsics.

Let's take `Vector256<T>`, which I'll be using exclusively in this series, as an example; `Vector256<T>` can be used **only** with the following primitive types:

<table class="fragment">
<thead><th style="border: none"><code>typeof(T)</code></th>
<th/>
<th style="border: none"># Elements</th>
<th style="border: none"></th>
<th style="border: none">Element Width (bits)</th>
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

No matter which type of the supported primitive set we'll choose, we'll end up with a total of 256 bits, or the underlying SIMD register width.  
Now that we've kind of figured out of vector types/registers are represented in C#, let's perform some operations on them.

### A few Vectorized Instructions for the road

Armed with this new understanding and knowledge of `Vector256<T>` we can move on and start learning a few vectorized instructions.

Chekhov famously said: "If in the first act you have hung a pistol on the wall, then in the following one it should be fired. Otherwise, don't put it there". Here are seven loaded AVX2 pistols; rest assured they are about to fire in the next act. I’m obviously not going to explain all 1,000+ intrinsics mentioned before, if only not to piss off Anton Chekhov. We will **thoroughly** explain the ones needed to get this party going.  
Here's the list of what we're going to go over:

| x64 asm       | Intel                                                        | CoreCLR                                                      |
|:---------------|:--------------------------------------------------------------:|-------------------------------------------------------------:|
| `vbroadcastd` | [`_mm256_broadcastd_epi32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_broadcastd_epi32&expand=542) | [`Vector256.Create(int)`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.vector256.create?view=netcore-3.0#System_Runtime_Intrinsics_Vector256_Create_System_Int32_) |
| `vlddqu`      | [`_mm256_lddqu_si256`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_lddqu_si256&expand=3296) | [`Avx.LoadDquVector256`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx.loaddquvector256?view=netcore-3.0#System_Runtime_Intrinsics_X86_Avx_LoadDquVector256_System_Int32__) |
| `vmovdqu`     | [`_mm256_storeu_si256`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_storeu_si256&expand=5654) | [`Avx.Store`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx.store?view=netcore-3.0#System_Runtime_Intrinsics_X86_Avx_Store_System_Int32__System_Runtime_Intrinsics_Vector256_System_Int32__) |
| `vpcmpgtd`    | [`_mm256_cmpgt_epi32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_cmpgt_epi32&expand=900) | [`Avx2.CompareGreaterThan`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx2.comparegreaterthan?view=netcore-3.0#System_Runtime_Intrinsics_X86_Avx2_CompareGreaterThan_System_Runtime_Intrinsics_Vector256_System_Int32__System_Runtime_Intrinsics_Vector256_System_Int32__) |
| `vmovmskps`   | [`_mm256_movemask_ps`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_movemask_ps&expand=3870) | [`Avx.MoveMask`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx.movemask?view=netcore-3.0#System_Runtime_Intrinsics_X86_Avx_MoveMask_System_Runtime_Intrinsics_Vector256_System_Single__) |
| `popcnt`      | [`_mm_popcnt_u32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm_popcnt_u32&expand=4378) | [`Popcnt.PopCount`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.popcnt.popcount?view=netcore-3.0#System_Runtime_Intrinsics_X86_Popcnt_PopCount_System_UInt32_) |
| `vpermd`      | [`_mm256_permutevar8x32_epi32`](https://software.intel.com/sites/landingpage/IntrinsicsGuide/#text=_mm256_permutevar8x32_epi32&expand=4201) | [`Avx2.PermuteVar8x32`](https://docs.microsoft.com/en-us/dotnet/api/system.runtime.intrinsics.x86.avx2.permutevar8x32?view=netcore-3.0#System_Runtime_Intrinsics_X86_Avx2_PermuteVar8x32_System_Runtime_Intrinsics_Vector256_System_Int32__System_Runtime_Intrinsics_Vector256_System_Int32__) |

I understand that for first time readers, this list looks like I'm just name-dropping lots of fancy code names to make myself sound smart, but the unfortunate reality is that we *kind of need* to know all of these, and here is why: On the right column I've provided the actual C# Intrinsic function we will be calling in our code and linked to their docs. But here's a funny thing: There is no "usable" documentation on Microsoft's own docs regarding most of these intrinsics. All those docs do is simply point back to the Intel C/C++ intrinsic name, which I've also provided in the middle column, with links to the real documentation, the sort that actually explains what the instruction does with pseudo code. Finally, since we're practically writing assembly code anyways, and I can guarantee we'll end up inspecting JIT'd code down the road, I provided the x86 assembly opcodes for our instructions as well.[^3]
Now, What does each of these do? Let's find out...

As luck would have it, I was blessed with the ultimate power of wielding SVG animations, so most of these instructions will be accompanied by an animation *visually explaining* them.  
<span class="uk-label">Hint</span>: These animations are triggered by your mouse pointer / finger-touch inside them. The animations will immediately freeze once the pointer is out of the drawing area, and resume again when inside. Eventually, they loop over and begin all over...  
From here-on, I'll use the following icon when I have a thingy that animates:<br/><object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/play.svg"></object>
{: .notice--info}

#### Vector256.Create(int value)

<div markdown="1">
<div markdown="1" class="stickemup">
<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/inst-animations/vbroadcast-with-hint.svg"></object>
</div>

We start with a couple of simple instructions, and nothing is more simple than this first: This intrinsic accepts a single scalar value and simply “broadcasts” it to an entire SIMD register, this is how you’d use it:

```csharp
Vector256<int> someVector256 = Vector256.Create(0x42);
```

This will load up `someVector256` with 8 copies of `0x42` once executed, and in x64 assembly, the JIT will produce something quite simple:

```nasm
vmovd  xmm0, rax          ; 3 cycle latency / 1 cycle throughput
vpbroadcastd ymm0, xmm0   ; 3 cycle latency / 1 cycle throughput
```

This specific intrinsic is translated into two intel opcodes, since there is no direct single instruction that performs this.
</div>

#### Avx2.LoadDquVector256 / Avx.Store

<div markdown="1">
<div markdown="1" class="stickemup">
<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/inst-animations/lddqu-with-hint.svg" ></object>
</div>

Next up we have a couple of brain dead simple intrinsics that we use to read/write from memory into SIMD registers and conversely store from SIMD registers back to memory. These are amongst the most common intrinsics out there, as you can imagine:

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

I only included an SVG animation for `LoadDquVector256`, but you can use your imagination and visualize how `Store` simply does the same thing in reverse.
</div>

#### CompareGreaterThan

<div markdown="1">
<div markdown="1" class="stickemup">
<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/inst-animations/vpcmpgtd-with-hint.svg" ></object>
</div>

`CompareGreaterThan` does an *n*-way, element-by-element *greater-than* (`>`) comparison between two `Vector256<T>` instances. In our case where `T` is really `int`, this means comparing 8 integers in one go, instead of performing 8 comparisons serially!

But where is the result? In a new `Vector256<int>` of course! The resulting vector contains 8 results for the corresponding comparisons between the elements of the first and second vectors. Each position where the element in the first vector was *greater-than* (`>`) the second vector, the corresponding element in the result vector gets a `-1` value, or `0` otherwise.  
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

</div
>
#### MoveMask

<div markdown="1">
<div markdown="1" class="stickemup">
<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/inst-animations/vmovmskps-with-hint.svg"></object>
</div>

Another intrinsic which will prove to be very useful is the ability to extract some bits from a vectorized register into a normal, scalar one. `MoveMask` does just this. This intrinsic takes the top-level (MSB) bit from every element and moves it into our scalar result:

```csharp
Vector256<int> data;
int result = Avx.MoveMask(data.AsSingle());
```

There’s an oddity here, as you can tell by that awkward `.AsSingle()` call, try to ignore it if you can, or hit this footnote[^4] if you can't. The assembly instruction here is exactly as simple as you would think:

```nasm
vmovmskps rax, ymm2  ; 5 cycle latency
                     ; 1 cycle throughput
```

</div>

#### PopCount

`PopCount` is a very powerful intrinsic, which [I've covered extensively before]({% post_url 2018-08-19-netcoreapp3.0-intrinsics-in-real-life-pt2 %}): `PopCount` returns the number of `1` bits in a 32/64 bit primitive.  
In C#, we would use it as follows:

```csharp
int result = PopCnt.PopCount(0b0000111100110011);
// result == 8
```

And in x64 assembly code:

```nasm
popcnt rax, rdx  ; 3 cycle latency
                 ; 1 cycle throughput
```

In this series, `PopCount` is the only intrinsic I use that is not purely vectorized[^5].

#### PermuteVar8x32

<div markdown="1">
<div markdown="1" class="stickemup">
<object stle="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/inst-animations/vpermd-with-hint.svg"></object>
</div>

`PermuteVar8x32` accepts two vectors: source, permutation and performs a permutation operation **on** the source value *according to the order provided* in the permutation value. If this sounds confusing go straight to the visualization below...

```csharp
Vector256<int> data, perm;
Vector256<int> result = Avx2.PermuteVar8x32(data, perm);
```

While technically speaking, both the `data` and `perm` parameters are of type `Vector256<int>` and can contain any integer value in their elements, only the 3 least significant bits in `perm` are taken into account for permutation of the elements in `data`.  
This should make sense, as we are permuting an 8-element vector, so we need 3 bits (2<sup>3</sup> == 8) in every permutation element to figure out which element goes where... In x64 assembly this is:

```nasm
vpermd ymm1, ymm2, ymm1 ; 3 cycles latency
                        ; 1 cycles throughput
```

</div>

### That’s it for now

This post was all about laying the groundwork before this whole mess comes together.  
Remember, we’re re-implementing QuickSort with AVX2 intrinsics in this series, which for the most part, means re-implementing the partitioning function from our scalar code listing in the previous post.  
I’m sure wheels are turning in many heads now as you are trying to figure out what comes next…  
I think it might be a good time as any to end this post and leave you with a suggestion: Try to take a piece of paper or your favorite text editor, and see if you can some cobble up these instructions into something that can partition numbers given a selected pivot.

When you’re ready, head on to the [next post]({% post_url 2019-08-20-this-goes-to-eleven-pt3 %}) to see how the whole thing comes together, and how fast we can get it to run with a basic version…

---------
[^0]: To be clear, some of these are intrinsics in unreleased processors, and even of those that are all released in the wild, there is no single processor support all of these...
[^1]: CoreCLR supports roughly everything up to and including the AVX2 intrinsics, which were introduced with the  Intel Haswell processor, near the end of 2013.
[^2]: In general, auto-vectorizing compilers are a huge subject in their own, but the bottom line is that without completely changing the syntax and concepts of our programming language, there is very little that an auto-vectorizing compiler can do with existing code, and making one that really works often involves designing programming language with vectorization baked into them from day one. I really recommend reading [this series about Intel's attempt](https://pharr.org/matt/blog/2018/04/30/ispc-all.html) at this space if you are into this sort of thing.
[^3]: Now, If I was in my annoyed state of mind, I'd bother to mention that [I personally always thought](https://github.com/dotnet/corefx/issues/2209#issuecomment-317124449) that introducing 200+ functions with already established names (in C/C++/rust) and forcing everyone to learn new names whose only saving grace is that they look BCL*ish* to begin with was not the friendliest move on Microsoft's part, and that trying to give C# names to the utter mess that Intel created in the first place was a thankless effort that would only annoy everyone more, and would eventually run up against the inhumane names Intel went for (Yes, I'm looking at you `LoadDquVector256`, you are not looking very BCL-ish to me with the `Dqu` slapped in the middle there : (╯°□°)╯︵ ┻━┻)... But thankfully, I'm not in my annoyed state.

[^4]: While this looks like we’re really doing “something” with our `Vector256<int>` and somehow casting it do single-precision floating point values, let me assure you, this is just smoke and mirrors: The intrinsic simply accepts only floating point values (32/64 bit ones), so we have to “cast” the data to `Vector256<float>`, or alternatively call `.AsSingle()` before calling `MoveMask`. Yes, this is super awkward from a pure C# perspective, but in reality, the JIT understands these shenanigans and really ignores them completely.
[^5]: By the way, although this intrinsic doesn't accept nor return one of the SIMD registers / types, and considered to be a non-vectorized intrinsic as far as classification goes, as far as I'm concerned bit-level intrinsic functions that operate on scalar registers are just as "vectorized" as their "pure" vectorized sisters, as they mostly deal with scalar values as vectors of bits.
