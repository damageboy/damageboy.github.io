---
title: CoreCLR 3.0 Intrinsics
theme: "solarized"
transition: "zoom"
highlightTheme: "androidstudio"
logoImg: "dotnet-core.svg"
slideNumber: true
mouseWheel: true
parallaxBackgroundImage: "die-shot-slice-1.webp"
parallaxBackgroundHorizontal: 200
parallaxBackgroundVertical: 200
enableTitleFooter: false
---

# CoreCLR 3.0 Intrinsics

<aside class="notes">
Hi Everyone,
This is CoreCLR 3.0 Intrinsics
</aside>

---

## getent passwd $USER

<small>dmg:*:666:666:Dan Shechter:/home/dmg:/usr/bin/zsh</small>

<p class="fragment fade-in-then-out">
CTO of a high-frequency trading* firm that trades global markets from inside exchanges.
</p>

<p class="fragment">
Also, *nix programmer that likes <span class="fragment highlight-blue">low-level & perf</span> and whose been around the block:
<span class="fragment highlight-blue">Windows/Linux kernel programming, </span><span class="fragment highlight-red">Hypervisors</span>
</p>

<table>
<tr>
<td style="border-right: 1px solid black; padding-top: 0px; padding-bottom: 0px">
<a href="https://bits.houmus.org">
<object style="margin: auto;pointer-events: none;" type="image/svg+xml" width="48"  data="logos/atari.svg"></object>
</a>
</td>
<td style="border-right: 1px solid black; padding-top: 0px; padding-bottom: 0px">
<a href="http://twitter.com/damageboy">
<object style="margin: auto;pointer-events: none;" type="image/svg+xml" width="48"  data="logos/twitter.svg"></object>
</a>
</td>
<td style="padding-top: 0px; padding-bottom: 0px">
<a href="https://github.com/damageboy">
<object style="margin: auto;pointer-events: none;" type="image/svg+xml" width="48"  data="logos/github.svg"></object>
</td>
</tr>
</table>

<aside class="notes">
I'm Dan, a.k.a Damageboy on these fine platforms below,

In my day job, I'm a CTO for a high frequency trading firm...
So this means a lot of things depending on context, but in our context here, this means
that I'm part of team that gets to make more money hen they succeed in running their code faster...

Other than that, old time unix programmer, with a passion for perf,
have done windows/linux kernel in my past, and also irreversibly traumatized by writing a hypervisor in 2003.
</aside>

---

# Why You're Here

<aside class="notes">

So you know those talks where the person on stage has these hopeful messages filled with positivity?
So I want to do the eastern-european version of my anscestors of that: Where a total stranger walks up
to you and tells you that everything is horrible and everything is falling apart...
And then offers you a small glimpse of hope.

</aside>

---

<img style="background: #FFF" width="150%" class="plain" src="single-threaded-perf-computer-arch.svg" />

<span style="font-size: small;">From: "Computer Architecture: A Quantitative Approach, 6<sup>th</sup> Edition</span>

<aside class="notes">

So: Here is the last 40 years of single threaded performance improvemnets.
After a first happy couple of decades, at 2003, we're start seeing an ever increasing
slowdown in this rate, even thogh transistor density has been doubling all the way
till 2015 until we reach our current time, which is the dark ages at 3.5% per year.

Now, no one knows for sure what the future holds,
But I think we can all agree that the present sucks.

- Dennard observes that transistor dimensions are scaled by 30% (0.7x) every technology generation, thus reducing their area by 50%. This reduces the delay by 30% (0.7x) and therefore increases operating frequency by about 40% (1.4x). Finally, to keep the electric field constant, voltage is reduced by 30%, reducing energy by 65% and power (at 1.4x frequency) by 50%.[note 1] Therefore, in every technology generation the transistor density doubles, the circuit becomes 40% faster, and power consumption (with twice the number of transistors) stays the same.

- Amdahl's law can be formulated in the following way:
  ${\displaystyle S_{\text{latency}}(s)={\frac {1}{(1-p)+{\frac {p}{s}}}}}$
  
  where:

  - $S_{\text{latency}}$ is the theoretical speedup of the execution of the whole task;
  - s is the speedup of the part of the task that benefits from improved system resources;
  - p is the proportion of execution time that the part benefiting from improved resources originally occupied.

</aside>

---

<blockquote>
<span class="fragment fade-down">
"The reason processor performance is sub-linear with transistor count is <span class="fragment fade-down">[because] it's limited by <b>unpredictability</b>:</span>
<span class="fragment fade-in" style="color: red;">Branch predictability,</span><span class="fragment fade-in" style="color: blue;"> Data-predictability,</span> <span class="fragment fade-in" style="color: green;"> Instruction predictability."</span>
</span>
</blockquote>

[Jim Keller](https://en.wikipedia.org/wiki/Jim_Keller_(engineer))
From: [Moore's Law is Not Dead](https://youtu.be/oIG9ztQw2Gc?t=1788)

<aside class="notes">
But I did say I also have some hope to offer you:

This is a quote by Jim Keller who is a famous CPU architecht,
Who gave this ironically titled talk: "Moore's law is not dead", where he says
that the reason for this slowdown, is unpredictability:
branch, data, and instrunction unpredictability.

So the flip-side of this, is my message of hope to you: that by providing the CPU with predictability
we can definitely improve our odds at running code faster, and a very effective
way of doing this is with intrinsics...
</aside>

---

## Branch Prediction

- A single core executes hundreds of instructions at any given moment...
- To do this, CPUs guess the target of branches!           { .fragment }
  - It usually does a good job                             { .fragment }
- But when it fails we get penalized                       { .fragment .fade-up }
  - ~15 cycles for a single mis-prediction!                { .fragment .fade-up }

<aside class="notes">

A modern CPU is processing hundreds of instructions in some form or another
at any given moment.

To pull this crazy feat, it has to guess the result of the conditions in our code.
So every if, while etc. has to be predicted, for the CPU not to be out of work.

Normally, it can do a pretty good job at this.
But it can't always be successful.
I magine that we feed it with purely random data.
It won't do any better than flipping a coin.

Every time it fails, the penalty is huge: 15 cycles on a modern Intel CPU for example.
To make it worse, in many cases, the code behind that branch is 1-2 cycles long...

</aside>

---

Now that I've got you ~~scared~~ motivated enough...

Let's get busy!

<aside class="notes">
So now that you're scared...
</aside>

---

## Intrinsics in  English

A way to directly embed **specific** CPU instructions via special, *fake* method calls that the JIT replaces at code-generation time

<aside class="notes">
What are these intrinsics we're going to fix the world with?

Simply speaking, intrinsics are fake functions we call in our code, that the JIT
will replace, for us, with a very specific 1-2 CPU instructions. So you can think of a
bit like writing assembly code through function calls...

But why do we need it?
</aside>

---

Used to expose processor functionality that *doesn't* map well to the language:

<ul>
<span class="fragment"><li>Atomic operations</li></span>
<span class="fragment"><li>System-Programming (e.g. kernel mode)</li></span>
<span class="fragment"><li>Crypto instructions</li></span>
<span class="fragment"><li>Niche instructions</li></span>
<span class="fragment"><span class="fragment highlight-blue"><span class="fragment highlight-green"><span class="fragment highlight-red"><li><b>Vectorization</b></li></span></span></span></span><span class="fragment"> - Instructions that work on vectors</span>
</ul>

<aside class="notes">
Traditionally, CPUs always had lot of functionality that can't be mapped easily
into our programming languages, There are about 1,200 intrinsics in Intel CPUs alone, and
they cover this entire gamut, but if we're honest, it all comes down to vectorization.
That's about 96% of those 1,200 on Intel CPUs!
</aside>

---

## Vectorization 101

<object style="margin: auto" type="image/svg+xml" data="vec101.svg"></object>

Usually 1-3 CPU cycles!
{ .fragment }

---

## Math is hard, let's go sorting!

I needed to sort numbers, and really fast.

<p class="fragment zoom" data-fragment-index="1">
"Eh, I'll rewrite <span class="fragment fade-in" style="position:inline; margin-left: auto; margin-right: auto; left: 0; right: 0;" data-fragment-index="2">Array.Sort<sup>*</sup>.</span>
<span class="fragment fade-out" style="position:relative; margin-left: auto; margin-right: auto; left: -200px; right: 0;" data-fragment-index="2">QuickSort.</span>
<span class="fragment fade-down" style="position:relative; margin-left: auto; margin-right: auto; left: -200px; right: 0;" data-fragment-index="3">With intrinsics...</span>
<span class="fragment fade-down" data-fragment-index="4">How bad could it be?"</span>
</p>

<p class="fragment zoom" data-fragment-index="5">
<i>6 months later, and I'm still at it...</i>
</p>

<span class="fragment fade-up" data-fragment-index="6">
<b>But,</b>
</span>

<aside class="notes">
Let's go and sort some numbers!

So, a while back I decided I'd tackle a problem that both close to my heart
and my line of work...

I thought to my self, "I'll re-write quicksort, or really Array.Sort, because they're
very close to eachother. With intrinsics! I mean, really, how bad could it be?

So, that was 5 months ago! And I'm still having way too much fun with this...

But, I can share here something with you, thats...
</aside>

---

<!-- .slide: data-transition="none" data-background-transition="none" data-background="cats/t2.gif" -->

---

# It's 9x faster

<aside class="notes">
6x faster!
</aside>

---

<img class="plain" style="position: relative; left:  400px; top:-075px; width: 50%; height: 50%" src="cats/cat1.gif"/>
<img class="plain" style="position: relative; left: -500px; top:-500px; width: 50%; height: 50%" src="cats/cat2.gif"/>
<img class="plain" style="position: relative; left: -300px; top:-600px; width: 75%; height: 75%" src="cats/cat3.gif"/>
<img class="plain" style="position: relative; left: -075px; top:-1400px; width: 50%; height: 50%" src="cats/cat4.gif"/>
<img class="plain" style="position: relative; left:  200px; top:-1350px; width: 75%; height: 75%" src="cats/cat5.gif"/>

---

## Why QuickSort?

- Universally known                                    { .fragment }
- Non-trivial use of intrinsics                        { .fragment .fade-down }
- Pretty close to <code>Array.Sort</code><sup>*</sup>  { .fragment .fade-up   }

<aside class="notes">
Now if you think you kind of remember how quicksort works, could you raise your hand and keep it up?

Great, now those of you who've implemented it, even if you were drunk and it was 20 years ago, can you keep you hand up?

OK!

So, as you can see, it's universally known.

Also, as we'll see, this will be a non-trivial use of intrinsics.
It's not one of those "Let's sum all the numbers in a array in 5 lines of code, then pat ourselves on the shoulder to say "good job" and move on...

And finally, as I've mentioned, our baseline for comparison isn't something we copy-pasted from stack-overflow, it's the actual
code we all rely on in the class libraries for .NET

</aside>

---

## Refresher

- QuickSort uses a *divide-and-conquer* approach
  - It's recursive                                         { .fragment }
- Has average O(*n* log *n*) comparisons for *n* items     { .fragment .fade-down }
- Performs an in-place sort                                { .fragment .fade-up }

<aside class="notes">
So, a quick refresher about quicksort:

It uses a divide an conquer approach. So it's recursive
Has n*log(n) comparisons to sort N items

And most importantly, it's an in-place sort, so we don't need to allocate more memory
to sort numbers.
This last point, as we'll see, it great for users, but is going to haunt me..
</aside>

---

1. Pick a *pivot* value
2. Partition the array around the pivot value { .fragment .highlight-red }
3. Recurse on the left side of the pivot
4. Recurse on the right side of the pivot

<aside class="notes">
So in quicksort we:

1. Pick a pivot: which really means pick some number from the array. can really be anything
2. Re-arrange the array so that all the numbers on the left are smaller than the pivot,
   Then we have the pivot, in its final resting place, and then all the numbers larger that
   the pivot! This is really the big thing we will be working on, since other than that we simple:
3. Recurse on the left hand side of the new pivot position
4. And finally recurse on the right hand side.

It's that simple!
</aside>

---

```csharp
int Partition(int[] array, int pivot, int left, int right)
{
    while (left <= right) {
        while (array[left] < pivot) left++;
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

<span class="code-presenting-annotation fragment current-only" data-code-focus="3-7">Branches, Branches Everywhere! </span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="4-5">👎 Unpredictable 👎</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="3,7">👍 Predictable 👍</span>

<aside class="notes">
Finally, before moving on to intrinsics: Here's the code for a pretty standard partition function.

We can see that it scan the array from left to right, comparing and swapping elements.

It's easy to see there are 4 branches in this function.
But's they're not the same.
These two, are pretty horrible, as we're branching based on actual, unsorted, so called random data
that we were tasked to sort.. So these are pretty bad for the CPU to predict, if we remember our observation
about unpredictability!

On the other hand, these two branches are rather easy to predict, since they're simply
true, 99% of the time.
</aside>

---

## By the numbers

<small>

| Stat              | 100  | 1K    | 10K    |   100K    | **1M**         |      10M    |
| ------------------|:---- |:----- |:------ |:--------- |:-------------- |:----------- |
| Max Depth         | 8    | 14    | 21     | 28        | **35**         | 42          |
| # Partitions      | 33   | 342   | 3,428  | 34,285    | **342,812**    | 3,428,258   |
| # Comparisons     | 422  | 6,559 | 89,198 | 1,128,145 | **13,698,171** | 155,534,152 |

</small>

<aside class="notes">
I also collected some stats from running quick-sort,
And for example, for 1M elements, in this table you can see
how deep the recursion is, how many calls to the partition function there are
and how many comparisons are involved, and it's clear there is a lot of work involved here.
</aside>

---

## Plan

Redo `Partition` with vectorized intrinsics.

- What intrinsics do we use?
- How do they work?

<aside class="notes">
So our plan, is obviously to use vectorized or SIMD intrinsics to
rewrite the partition function we saw before.

But what are those? How to they work?
</aside>

---

## How?

How can an instruction operate on a vector?

Does it operate <i>directly</i> on memory? { .fragment .fade-down}

Generally: <b>No!</b>                      { .fragment .fade-up }

<aside class="notes">
What does it really mean vectorized instruction?

Do these instruction simply take a pointer to memory?

So, in general: No!

Instead:
</aside>

---

## Vectors Types / Registers

<p>
These intructions operate on <i>special</i> vector types that are supported at the CPU level: <span class="fragment">registers</span>
</p>

<p class="fragment">
Vector registers have constant size (in bits).
</p>

<aside class="notes">
All of these instruction accept and/or return special vector types, at the CPU level.
So really: registers!

The registers have a constant width in bits, let's look at what's there:
</aside>

---

## SIMD registers in CoreCLR 3.0

C# vectorized intrinsics accept and return these types:

<table><thead><tr>
<th style="text-align:left;"  ><span class="fragment" data-fragment-index="1"><code>CoreCLR</code></span></th>
<th style="text-align:right;" ><span class="fragment" data-fragment-index="2"><code>Intel</code></span></th>
</tr></thead>
<tbody><tr>
<td style="text-align:left;border:none">
<span class="fragment" data-fragment-index="1"><a href="https://github.com/dotnet/coreclr/blob/master/src/System.Private.CoreLib/shared/System/Runtime/Intrinsics/Vector64_1.cs"><code>Vector64&lt;T&gt;</code></a></span>
</td>
<td style="text-align:right;border:none">
<span class="fragment" data-fragment-index="2"><code>mm0-mm7</code></span>
</td></tr><tr>
<td style="text-align:left;border:none">
<span class="fragment" data-fragment-index="1"><a href="https://github.com/dotnet/coreclr/blob/master/src/System.Private.CoreLib/shared/System/Runtime/Intrinsics/Vector128_1.cs" ><code>Vector128&lt;T&gt;</code></a></span>
</td>
<td style="text-align:right;border:none">
<span class="fragment" data-fragment-index="2"><code>xmm0-xmm15</code></span>
</td></tr><tr>
<td style="text-align:left;border:none">
<span class="fragment" data-fragment-index="1"><a href="https://github.com/dotnet/coreclr/blob/master/src/System.Private.CoreLib/shared/System/Runtime/Intrinsics/Vector256_1.cs"><code>Vector256&lt;T&gt;</code></a></span>
</td>
<td style="text-align:right;border:none">
<span class="fragment" data-fragment-index="2"><code>ymm0-ymm15</code></span>
</td></tr></tbody></table>

Where `T` is some primitive type.
{ .fragment }
<aside class="notes">
So in CoreCLR, we have these 3 vector types: Vector 64, 128, and 256 of T.
These are special types recognized by the JIT, just like int or double are special.
</aside>

---

## Example:

`Vector256<T>` can be:
<table class="fragment">
<tr><td style="text-align:center;border: none"><code>byte / sbyte</code></td>  <td style="border: none">⮚</td> <td style="border: none">32 x 8b</td><td style="border: none"> == 256b</td></tr>
<tr><td style="border: none"><code>short / ushort</code></td><td style="border: none">⮚</td> <td style="border: none">16 x 16b</td><td style="border: none"> == 256b</td></tr>
<tr>
<td style="text-align:center;border: none; color: blue"><code>int / uint</code></td>
<td style="border: none; color: blue">⮚</td> <td style="border: none; color: blue">8 x 32b</td>
<td style="border: none; color: blue">== 256b</span></td></tr>  
<tr><td style="text-align:center;border: none"><code>long / ulong</code></td>  <td style="border: none">⮚</td> <td style="border: none">4 x 64b</td><td style="border: none"> == 256b</td></tr>
<tr><td style="text-align:center;border: none"><code>float</code></td>         <td style="border: none">⮚</td> <td style="border: none">8 x 32b</td><td style="border: none"> == 256b</td></tr>
<tr><td style="text-align:center;border: none"><code>double</code></td>        <td style="border: none">⮚</td> <td style="border: none">4 x 64b</td><td style="border: none"> == 256b</td></tr>
</table>

<aside class="notes">
Let's take 256 as an example, since we'll use it for the rest of the talk:

As you can see, we can use all these various primitive types instead of T, and then we get anywhere
from 32 down to 4 elements per such vector! But in all cases, we will end up with 256 bits in total
which is the size of the vector.
</aside>

---

## Vectorized Partition Block

<ul>
<li>We're going to partition 8 x <code>int</code>s at a time</li>
<span class="fragment fade-up">
<ul>
<li>inside a Vector256</li>
</ul>
</span>
<span class="fragment fade-up"><li>Load <span class="fragment fade-up">⮚ Compare <span class="fragment fade-up">⮚ Permute <span class="fragment fade-up">⮚ Store</span></span></span></li>
<span class="fragment fade-up"><li>With no branching<span class="fragment fade-up"><span style="color: blue">(!)</span></span></li></span>
</ul>

---

<object style="margin: auto" type="image/svg+xml" data="block1.svg"></object>

---

## Now what?

- `mask` tells us which element goes where!
- We could loop over the bits in the mask       { .fragment .fade-down }
  - Back to square one: 8-branches              { .fragment .fade-down }
- I did not fly all the way to Moscow for this! { .fragment .fade-up   }

---

## Famous Quote

> Give me a lever long enough and a fulcrum on which to place it, and I shall move the world

<p style="font-size: x-large; text-align: right">
- Synagoge, Book VIII, 340 A.D.
</p>

<img src="archimedes.jpg"/>

---

## Less Famous Quote

> Give me vectorized intrinsics and a large enough look-up table, and I can make anything 4x faster

<p style="font-size: x-large; text-align: right">
- Intel Software Optimization Guide, 2003 A.D.
</p>

<img src="archimedes.jpg"/>

<p class="fragment fade-up">
<object class="plain" style="position: relative; left:  -30px; top:-400px; width: 7%; height: 7%" data="troll-face.svg"></object>
<object class="plain" style="position: relative; left:  90px; top:-270px; width: 20%; height: 20%" data="intel-inside.svg"></object>
</p>

---

## Permutation Tables

- There are 256 possible mask values (2<sup>8</sup>)
- We can precompute all permutations in a table         { .fragment .fade-down }
- Each permutation entry will provide the correct order
  for a given mask                                      { .fragment .fade-down }
- The table is simply part of the source code           { .fragment .fade-up}

---

## 8-Way Permute

<p style="font-size: x-large;text-align: left;">C#:</p>

```csharp
Vector256<int> data, perm;
Vector256<int> result = Avx2.PermuteVar8x32(data, perm);
```

<p style="font-size: x-large;text-align: left;">asm:</p>

```x86asm
vpermd ymm1, ymm2, ymm1 ; 3 cycle latency, 1 cycle throughput
```

<object style="margin: auto" type="image/svg+xml" data="inst-animations/vpermd.svg"></object>

<aside class="notes">

- There's little to say here on the C#
- Or the assembly
- But this is a clear "one picture is worth 1000 words" type of situation.

</aside>

---

```csharp
static int[] PermTable => new[] {
    0, 1, 2, 3, 4, 5, 6, 7,     // 0   => 0b00000000
    // ...
    3, 4, 5, 6, 7, 0, 1, 2,     // 7   => 0b00000111
    // ...
    0, 2, 4, 6, 1, 3, 5, 7,     // 170 => 0b10101010
    // ...
    0, 1, 2, 3, 4, 5, 6, 7,     // 255 => 0b11111111
};
```

<span class="code-presenting-annotation fragment current-only" data-code-focus="2,8">Everything stays in place</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="4">Move 3 from left to right</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="6">4/4 split</span>

---

<object style="margin: auto" type="image/svg+xml" data="block2.svg"></object>

---

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

<span class="code-presenting-annotation fragment current-only" data-code-focus="1">We generate a vectorized pivot, once per partition</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="3">Load 8 elements from somewhere.</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="5">Compare to pivot, cast to <code>Vector256&lt;float&gt;</code> (because <code>¯\\_(ツ)_/¯</code>)</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="4">Generate an 8-bit mask from the comparison result</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="7">Load permutation vector from table</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="6">Permute data (partition)</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="8">Store 8 elements to the left.</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="9">Store 8 elements to the right.</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="10">Count 1 bits ⮚ How many are elemenets are > than pivot.</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="11">Advance right by popCount.</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="12">Advance left by 8 - popCount.</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="3-12">8.5 cycle throughput</span>

<aside class="notes">
From the top:

- We start with creating a vectorized pivot value, once per partition call
- Somewhere, insdie a loop body we will shortly discuss, we continue to:
  - Load data
  - Compare it to the vectorized pivot 8-elements at a time
  - Compress the result back to a regular integer
  - Use that to load a pre-fabricate permutation entry! (which we will discuss)
  - Call the permutation intrinsic with our data and new order
  - Store all 8 elements to the left side
  - Store them again(!) to the right side
  - Call PopCount() to get the number of elements INSIDE our vector that belonged to the right!
  - Update the next write pointers using that pop count value!

</aside>

---

## OK, So now the vector is partitioned, what's next?

<aside class="notes">

So we finally have enough explosive to blow this joint!

We are going to read and partition 8 elements, at a time, within a vector256 inside the CPU!

Load, Compare, Permute, Write

But once we finish partitioning, we have, in our vector both elements larger and smaller,
So we write the resulting vector to BOTH sides of our original array!

We'll see that in a moment, but let's look at the code first

</aside>

---

<img src="fire.gif" width="350%" />

---

## stackalloc to the rescue

- We "cheat" just a bit: <code>¯\\_(ツ)_/¯</code>
- `stackalloc Vector256<int>[3]`   { .fragment .fade-down }
- Total temp memory: 96 bytes      { .fragment .fade-down }
  - Constant                       { .fragment .fade-up   }
  - No matter how much we sort     { .fragment .fade-up   }

---

## Outer loop

<object style="margin: auto" type="image/svg+xml" data="double-pumped-loop.svg"></object>
<object style="margin: auto" type="image/svg+xml" data="double-pumped-loop-legend.svg"></object>

---

## Yeah yeah, are we fast yet?

<canvas data-chart="line">

N,100,1K,10K,100K,1M,10M
ArraySort,              1   ,  1    , 1,   1    , 1   , 1
AVX2DoublePumpedNaive,  1.149425287,	1.666666667,	1.724137931,	2.564102564,	2.702702703,	2.702702703,

<!-- 
{ 
 "data" : {
  "datasets" : [
    { "borderColor": "#3e95cd", "borderDash": [], "fill": false },
    { "borderColor": "#8e5ea2", "borderDash": [], "fill": false }
  ]
 },
 "options": {
    "title": { "text": "Scalar Sorting - Scaled to Array.Sort", "fontColor": "#666666", "display": true },
    "scales": { 
      "yAxes": [{ 
        "ticks": { "min": 0.3, "fontColor": "#666666" },
        "scaleLabel": { "display": true, "labelString": "Scaling (%)", "fontColor": "#666666" },
        "gridLines": { "color": "#666666" }
      }],
      "xAxes": [{ 
          "ticks": { "fontColor": "#666666" },
          "scaleLabel": { "display": true, "labelString": "N (elements)", "fontColor": "#666666" },
          "gridLines": { "color": "#666666" }
          }]
    },
    "legend": { "display": true, "position": "bottom", "labels": { "fontSize": 14, "fontColor": "#666666" } },
    "title": { "position": "top" }
  }
}
-->

</canvas>

---

## What's the Speed Limit?

There's tons of stuff we could still do:

- Inspect MSIL                   { .fragment .fade-down }
- Inspect asm code               { .fragment .fade-down }
- Use HW counters                { .fragment .fade-down }
- Vectorization Tweaks           { .fragment .fade-up }
- Special code for small arrays  { .fragment .fade-up }

<object class="plain" style="position: relative; left:  0px; top:-480px; width: 75%; height: 75%" data="speed-limit-50-ns.svg"></object>
{ .fragment }

---

## MSIL

- C# compiler uses definite assignment analysis
  - CS0165: Use of unassigned local variable... { .fragment .fade-down }
- But tells JIT to initialize locals regardless { .fragment .fade-up }
- a.k.a .locals init                            { .fragment .fade-up }

---

```x86asm
  .method private hidebysig static int32*
    VectorizedPartitionInPlace(
      int32* left,
      int32* right,
      int32* tmp
    ) cil managed
  {
    .maxstack 3
    .locals init (
      [0] int32 pivot,
      ...
      [22] int32 v,
      [23] valuetype [System.Runtime]System.ReadOnlySpan`1<int32> V_23
    )
```

<span class="code-presenting-annotation fragment current-only" data-code-focus="9">Why?</span>

---

- There's a bunch of ways to remove this
- I chose [Fody.LocalsInit](https://github.com/ltrzesniewski/LocalsInit.Fody)  
  by [@Lucas_Trz](https://twitter.com/Lucas_Trz)  { .fragment .fade-down }
  - Checkout Fody!                                                                                                         { .fragment .fade-down }

---

## 5min hack

# 2-3% Improvement

---

## Inspecting ASM

```csharp
while (readRight >= readLeft) {
    int *nextPtr;
    if (readLeft   - writeLeft <=
        writeRight - readRight) {
        nextPtr = readLeft;
        readLeft += 8;
    } else {
        nextPtr = readRight;
        readRight -= 8;
    }
    var current = Avx.LoadDquVector256(nextPtr);
    //...
}
```

<span class="code-presenting-annotation fragment current-only" data-code-focus="1">Anything left to partition?</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="3-4">Which side is closer to being overwritten?</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="5-6">Pick left</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="8-9">Pick right</span>

<aside class="notes">
We already saw the assmebly code for the vectorized block,
but what about the rest of the code around it?
</aside>

---

## ASM Code

```x86asm
mov rcx, rdi    ; rdi -> readLeft
sub rcx, r12    ; r12 -> writeLeft
mov r8, rcx
sar r8, 0x3f
and r8, 0x3
add rcx, r8
sar rcx, 0x2
mov r9, rsi     ; rsi -> writeRight
sub r9, r13     ; r13 -> readRight
mov r10, r9
sar r10, 0x3f
and r10, 0x3
add r9, r10
sar r9, 0x2
cmp rcx, r9
```

<span class="code-presenting-annotation fragment current-only" data-code-focus="1-2,8-9">Load, Subtract...</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="15">Compare</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="3-7,10-14">What the...?</span>

---

Look at all that arithmetic... Why?

<p class="fragment">
The JIT is <b>not</b> optimizing the <code>int</code> pointer math
when comparing <i>differences</i>...
</p>

<p class="fragment">
We can get past this!
</p>

---

Before:

```csharp
if (readLeft   - writeLeft <=
    writeRight - readRight) {
    // ...
} else {
    // ...
}
```

After:

```csharp
if ((byte *) readLeft   - (byte *) writeLeft) <=
    (byte *) writeRight - (byte *) readRight)) {
    // ...
} else {
    // ...
}
```

---

## Much better

```x86asm
mov rcx, rdi    ; rdi -> readLeft
sub rcx, r12    ; r12 -> writeLeft
mov r9, rsi     ; rsi -> writeRight
sub r9, r13     ; r13 -> readRight
cmp rcx, r9
```

---

## Ugly, But Effective

# 6-9% Improvement!

---

## HW Counters

CPUs have HW counters that give stats!

```bash
$ perf stat -a --topdown ...
 Performance counter stats for 'system wide':
        retiring   bad speculation   frontend bound  backend bound
S0-C3 1    37.6%             37.3%            16.9%      13.2%
```

<span class="code-presenting-annotation fragment current-only" data-code-focus="4">Almost 40% of all branches are mis-predicted</span>

---

## Bad Speculation is Bad

- That "pick a side" logic is super bad for perf
  - We snuck in branch unpredictability!    { .fragment .fade-down }
  - 100% random data ⮚ Flip a coin          { .fragment .fade-down }
  - Every mis-prediction costs us 15 cycles { .fragment .fade-down }
  - Remember our block is 8 cycles!         { .fragment .fade-up }
  - We're doing nothing 50% of the time!    { .fragment .fade-up }

---

## Branch ⮚ Arithmetic

What if we can make the CPU run the same code, for both branches?

Turn the branch into a data dependency!

---

Before:

```csharp
int x, y;
if (x > y) {
    ptr += 8;
}
```

After:

```csharp
int x, y;
// + => 0, - => 0xFFFFFFFF
var condAsMask = (y - x) >> 31;
//Always perform the addition, sometimes with 0!
ptr += 8 & condAsMask;
```

---

## Poor-man's CMOV

This is a age-old technique of replacing badly speculated branches
with simple arithmetic.

It only works for <b>simple</b> branches.
{ .fragment .fade-down }

CoreCLR will hopefully learn to <code>cmov</code> in the future.
{ .fragment .fade-up }

---

## Unroll the code

- Change the ratio between work/branches predicted
- CPU prediction will continue to suck             { .fragment .fade-up data-fragment-index="1" }
  - But once every N partitioning operations       { .fragment .fade-up data-fragment-index="1" }
- We need to allocate more temporary space:        { .fragment .fade-up data-fragment-index="2" }
  - `2*N*Vector256<int>`                           { .fragment .fade-up data-fragment-index="2" }
  - `2*4*8*4 == 256 bytes`                         { .fragment .fade-up data-fragment-index="2" }

---

```csharp
while (readRight >= readLeft) {
    int *nextPtr;
    if (readLeft   - writeLeft <=
        writeRight - readRight) {
        nextPtr = readLeft;
        readLeft += 8*4;
    } else {
        nextPtr = readRight;
        readRight -= 8*4;
    }
    var L0 = LoadDquVector256(nextPtr + 0*8);
    var L1 = LoadDquVector256(nextPtr + 1*8);
    var L2 = LoadDquVector256(nextPtr + 2*8);
    var L3 = LoadDquVector256(nextPtr + 3*8);

    var m0 = (uint) MoveMask(CompareGreaterThan(L0, P).AsSingle());
    var m1 = (uint) MoveMask(CompareGreaterThan(L1, P).AsSingle());
    var m2 = (uint) MoveMask(CompareGreaterThan(L2, P).AsSingle());
    var m3 = (uint) MoveMask(CompareGreaterThan(L3, P).AsSingle());

    L0 = PermuteVar8x32(L0, GetIntPermutation(pBase, m0));
    L1 = PermuteVar8x32(L1, GetIntPermutation(pBase, m1));
    L2 = PermuteVar8x32(L2, GetIntPermutation(pBase, m2));
    L3 = PermuteVar8x32(L3, GetIntPermutation(pBase, m3));

    var pc0 = PopCount(m0);
    var pc1 = PopCount(m1);
    var pc2 = PopCount(m2);
    var pc3 = PopCount(m3);

    Store(writeRight, L0);
    writeRight -= pc0;
    Store(writeRight, L1);
    writeRight -= pc1;
    Store(writeRight, L2);
    writeRight -= pc2;
    Store(writeRight, L3);
    writeRight -= pc3;

    pc0 = 8 - pc0;
    pc1 = 8 - pc1;
    pc2 = 8 - pc2;
    pc3 = 8 - pc3;

    Store(writeLeft, L0);
    writeLeft +=  pc0);
    Store(writeLeft, L1);
    writeLeft += pc1);
    Store(writeLeft, L2);
    writeLeft += pc2);
    Store(writeLeft, L3);
    writeLeft += pc3);
}


```

<span class="code-presenting-annotation fragment current-only" data-code-focus="1-10">Can you spot the difference?</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="11-14">Load x4</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="16-19">Compare+MoveMask x4</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="21-24">Permute x4</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="26-29">PopCount x4</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="31-38">Store on the right x4</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="40-43">8 - PopCount x4</span>
<span class="code-presenting-annotation fragment current-only" data-code-focus="45-52">Store Left x4</span>

---

## Show me the money!

- Do we have less branch mis-predictions now?
  - Not really!                { .fragment .fade-down data-fragment-index="1" }
  - (Not in percent)           { .fragment .fade-down data-fragment-index="1" }
  - But less branches in total { .fragment .fade-down data-fragment-index="2" }
- Does it run faster though?   { .fragment .fade-up data-fragment-index="3" }

---

## Sure Does!

# 20-30% Improvement!

---

## Vectorization Tweaks

Let's kill two perf hogs with one crazy opt!

But what are we fixing?

---

## The Remainder problem

All vectorized code needs to deal with remainders.

<code>length % 8 != 0</code>

<object style="margin: auto" type="image/svg+xml" data="remainder.svg"></object>

Between 1-7 elements, handled with pure scalar code!

---

## Cacheline Boundaries

When reading memory, we really read from cache.

Single int ⮚ full cacheline: 64 bytes
{ .fragment .fade-up }

---

What if our data is across two cachelines?

<object style="margin: auto" type="image/svg+xml" data="cacheline-boundaries.svg"></object>
{ .fragment .fade-up }

The CPU needs to ask for 2<span style="color: red">(!)</span> cache-lines
{ .fragment .fade-up }

---

## Alignment

Normally, we shouldn't care!

- The JIT & allocator work to minimize this!          { .fragment .fade-down data-fragment-index="1" }
  - "Stuff" is aligned to pointer size: 4/8 bytes     { .fragment .fade-down data-fragment-index="2" }
  - When not: <sup>4</sup>⁄<sub>64</sub> ⮚ 6.25% rate  
    of cross-cacheline reads                          { .fragment .fade-down data-fragment-index="3" }
- But what about Vector256? (32-bytes)                { .fragment .fade-up   data-fragment-index="4" }
- Is this true for partitioning?                      { .fragment .fade-up   data-fragment-index="4" }

---

## No love!

Not a single pointer is naturally aligned to Vector256.
{ .fragment .fade-down }

With partitioning, the data dictates the alignment
{ .fragment .fade-down }


50% of our reads are reading 2 cachelines!!!
{ .fragment .fade-up }

---

## Two birds,  one stone

Let's fix both of these issues!

By doing **more** work!
{ .fragment }

---

<object style="margin: auto" type="image/svg+xml" data="overlap-partition.svg"></object>

- The safe approach is to align inward    { .fragment .fade-down data-fragment-index="1" }
  - Align with scalar code                { .fragment .fade-down data-fragment-index="1" }
  - No more remainder at the end          { .fragment .fade-down data-fragment-index="1" }
  - But this means more scalar work:  
    1-7 elements on each side!            { .fragment .fade-down data-fragment-index="2" }
- But what if we align outwards?          { .fragment .fade-down data-fragment-index="3" }
  - It's legal (But not trivial!)         { .fragment .fade-down data-fragment-index="3" }
  - 100% vectorized<sup>*</sup>!!!        { .fragment .fade-down data-fragment-index="4" }

---

## Totally worth it!

# 10%-20% improvement

## <sup>*</sup>When in cache      { .fragment .fade-up  }

---

## Small array sorting

Another common trick is to sort very small partitions directly without quicksort.

Array.Sort uses this.
{ .fragment .fade-down }

Can we? With Vectors?
{ .fragment .fade-up }

Yes we can!
{ .fragment .fade-up }

---

## Bitonic Sort

- Parallel Sorting Algorithm             { .fragment .fade-down data-fragment-index="1" }
  - Used a lot in GPUs                   { .fragment .fade-down data-fragment-index="1" }
- O(n * log<sup>2</sup>(n)) comparisons  { .fragment .fade-down data-fragment-index="2" }
- Generates 2 monotonic series           { .fragment .fade-up data-fragment-index="3" }
  - Increasing/Decreasing                { .fragment .fade-up data-fragment-index="3" }
  - Bitonic                              { .fragment .fade-up data-fragment-index="3" }
- No branches                            { .fragment .fade-up data-fragment-index="4" }

---

<object style="margin: auto" type="image/svg+xml" width="80%" data="bitonic-sort-animated.svg"></object>

---

## Final Speedup

<canvas data-chart="line">

N,100,1K,10K,100K,1M,10M
ArraySort,              1   ,  1    , 1,   1    , 1   , 1
AVX2DoublePumpedOverlinedUnrolledWithBitonicSort,  2.04,	4.80,	7.67,	8.73,	9.02,	8.93,

<!-- 
{ 
 "data" : {
  "datasets" : [
    { "borderColor": "#3e95cd", "borderDash": [], "fill": false },
    { "borderColor": "#8e5ea2", "borderDash": [], "fill": false }
  ]
 },
 "options": {
    "title": { "text": "Scalar Sorting - Scaled to Array.Sort", "fontColor": "#666666", "display": true },
    "scales": { 
      "yAxes": [{ 
        "ticks": { "min": 0.3, "fontColor": "#666666" },
        "scaleLabel": { "display": true, "labelString": "Scaling (%)", "fontColor": "#666666" },
        "gridLines": { "color": "#666666" }
      }],
      "xAxes": [{ 
          "ticks": { "fontColor": "#666666" },
          "scaleLabel": { "display": true, "labelString": "N (elements)", "fontColor": "#666666" },
          "gridLines": { "color": "#666666" }
          }]
    },
    "legend": { "display": true, "position": "bottom", "labels": { "fontSize": 14, "fontColor": "#666666" } },
    "title": { "position": "top" }
  }
}
-->

</canvas>

---

## In summary

We can and *should* re-approach even on age old problems to find ways
to increase the predictablity of our code by using instrinsics!

This is now available to us in C#/.NET as part of CoreCLR 3.0.
{ .fragment }

Be nice to your CPUs!
{ .fragment }

<aside class="notes">

Also, while I could not possible show it in this talk, there are many more optimizations
that I ended up , and probably more in the future all had to do with fighting this
monster that is hiding in plain sight insid our code.

</aside>

---

<section>
<h2><a id="user-content-links" class="anchor" href="#links" aria-hidden="true"></a>Links</h2>
<h3><a href="https://github.com/damageboy">github.com/damageboy</a></h3>
<h3><a href="https://twitter.com/damageboy">Twitter: @damageboy</a></h3>
<h3><a href="https://bits.houmus.org">Blog</a></h3>
</section>