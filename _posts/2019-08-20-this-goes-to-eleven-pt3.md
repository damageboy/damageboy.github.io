---
title: "This Goes to Eleven (Pt. 3/6)"
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
date: 2019-08-20 11:26:28 +0300
classes: wide
chartjs:
  scales:
    xAxes:
      - scaleLabel:
          display: true,
          labelString: "N (elements)"
          padding: 1
          lineHeight: 1.0
  legend:
    display: true
    position: right
    labels:
      fontSize: 14
  title:
    position: top
  plugins:
   zoom:
     pan:
       enabled: false
       mode: xy
     zoom:
       enabled: false
       mode: xy
       speed: 0.1
   deferred:
     xOffset: 150
     yOffset: 50%
     delay: 750
#categories: coreclr intrinsics vectorization quicksort sorting
---

Since there’s a lot to go over here, I’ve split it up into a few parts:

1. In [part 1]({% post_url 2019-08-18-this-goes-to-eleven-pt1 %}), we start with a refresher on `QuickSort` and how it compares to `Array.Sort()`.
2. In this part, we go over the basics of vectorized hardware intrinsics, vector types, and go over a handful of vectorized instructions we’ll use in part 3. We still won't be sorting anything.
3. In this part, we go through the initial code for the vectorized sorting, and we’ll start seeing some payoff. We finish agonizing courtesy of the CPU’s Branch Predictor, throwing a wrench into our attempts.
4. In [part 4]({% post_url 2019-08-21-this-goes-to-eleven-pt4 %}), we go over a handful of optimization approaches that I attempted trying to get the vectorized partitioning to run faster. We'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of all the remaining scalar code- by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization.
6. Finally, in part 6, I’ll list the outstanding stuff/ideas I have for getting more juice and functionality out of my vectorized code.

## Unstable Vectorized Partitioning + QuickSort

It’s time we mash all the new knowledge we picked up in the last posts about SIMD registers, instructions, and `QuickSort`ing into something useful. Here's the plan:

* Vectorized in-place partitioning:
  * First, we learn to take 8-element blocks, or units of `Vector256<int>`, and partition them with AVX2 intrinsics.
  * Then we take the world. We reuse our block to partition an entire array with a method I fondly name double-pumping, for processing large arrays in-place with this vectorized block.
* Once we've covered vectorized partitioning, we finish up with some innocent glue-code wrapping the whole thing to look like a proper `Array.Sort` replacement.

Now that we're doing our own thing, finally, It's time to address a baby elephant hiding in the room: Stable vs. Unstable sorting. I should probably bother explaining: One possible way to categorize sorting algorithms is with respect for their stability: Do they reorder *equal* values as they appear in the original input data or not. Stable sorting does not reorder, while unstable sorting provides no such guarantee. Stability *might* be a critical consideration when selecting the right sorting algorithm for a given task, for example:

* When sorting an array of structs/classes according to a key embedded as a member, we might want to preserve the order of the containing type.
* Likewise, stability *might be* a key consideration when sorting two arrays: keys and values, reordering both arrays according to the sorted order of the keys.

At the same time, stable sorting is a non-issue when:

* Sorting arrays of simple primitives, stability is meaningless:  
  (what does a "stable sort" of the array `[7, 7, 7]` even mean?)
* At other times, we *know* for a fact that our keys are unique. There is no unstable sorting for unique keys.
* Lastly, sometimes, *we just don’t care*. We're fine if our data gets reordered.

In the .NET/C# world, one could say that the landscape regarding sorting is a little unstable (pun intended):

* [`Array.Sort`](https://docs.microsoft.com/en-us/dotnet/api/system.array.sort?view=netcore-3.1) is unstable, as is clearly stated in the remarks section:
  > This implementation performs an unstable sort; that is, if two elements are equal, their order might not be preserved.
* On the other hand, [`Enumerable.OrderBy`](https://docs.microsoft.com/en-us/dotnet/api/system.linq.enumerable.orderby?view=netcore-3.1) is stable:
  > This method performs a stable sort; that is, if the keys of two elements are equal, the order of the elements is preserved.

In general, what I came up with in my full repo/nuget package are algorithms capable of doing both stable and unstable sorting. But with two caveats:

* Stable sorting is considerably slower than unstable sorting (But still faster than `Array.Sort`).
* Stable sorting is slightly more challenging/less elegant to explain.

Given all of this new information and the fact that I am only presenting pure primitive sorting, where there is no notion of stability anyway, for this series, I will be describing my unstable sorting approach. It doesn’t take a lot of imagination to get from here to the stable variant, but I’m not going to address this in these posts. It is also important to note that in general, when there is a doubt if stability is required (e.g., for key/value, `IComparer<T>`/`Comparison<T>`, or non-primitive sorting) we should assume that stable sorting is a minimal requirement.

### AVX2 Partitioning Block

Let's start with this “simple” block, describing what we do with moving pictures.

<span class="uk-label">Hint</span>: These animations are triggered by your mouse pointer / finger-touch inside them. The animations will immediately freeze once the pointer is out of the drawing area, and resume again when inside. Eventually, they loop over and begin all over.  
From here-on, I'll use the following icon when I have a thingy that animates:<br/><object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/play.svg"></object>
{: .notice--info}

<object style="margin: auto" width="100%" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/block-unified-with-hint.svg"></object>

Here is the same block, in more traditional code form:

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
var P = Vector256.Create(pivot); // Outside any loop, top-level in the function
...
[MethodImpl(MethodImplOptions.AggressiveInlining)]
static unsafe void PartitionBlock(int *dataPtr, Vector256<int> P,
                                  ref int* writeLeft, ref int* writeRight) {
    var data = Avx2.LoadDquVector256(dataPtr);
    var mask = (uint) Avx.MoveMask(
        Avx2.CompareGreaterThan(data, P).AsSingle());
    data = Avx2.PermuteVar8x32(data,
        Avx2.LoadDquVector256(PermTablePtr + mask * 8)));
    Avx.Store(writeLeft,  data);
    Avx.Store(writeRight, data);
    var popCount = PopCnt.PopCount(mask);
    writeRight -= pc;
    writeLeft  += 8 - pc;
}
```

</div>

That's a lot of cheese, let’s break this down:

* <span class="uk-label">L1</span>: Broadcast the pivot value to a vector I’ve named `P`. We’re just creating 8-copies of the selected pivot value in a SIMD register.  
Technically, this isn't really part of the block as this is this happens only *once* per partitioning function call! It's included here for context.  
* <span class="uk-label">L3-5</span>: We wrap our block into a static function. We forcefully inline it in various strategic places in the rest of our code.  
  This may look like an odd signature, but think of it as a way of not copy-pasting code with no performance penalty.
* <span class="uk-label">L6</span>: Load up data from somewhere in our array. `dataPtr` points to some unpartitioned data. `dataVec` is now loaded with data we need to partition, and that's the important bit.
* <span class="uk-label">L7-8</span>: Perform an 8-way comparison using `CompareGreaterThan`, then proceed to convert/compress the 256-bit result into an 8-bit value using the `MoveMask` intrinsic.  
  The goal here is to generate a **scalar** `mask` value, that contains a single `1` bit for every comparison where the corresponding data element was *greater-than* the pivot value and `0` bits for all others. If you are having a hard time following *why* this does this, you need to head back to the [2<sup>nd</sup> post](2019-08-19-this-goes-to-eleven-pt2.md) and read up on these two intrinsics/watch their animations.
* <span class="uk-label">L9-10</span>: Permute the loaded data according to a permutation vector; A-ha! A twist in the plot!  
  `mask` contains 8 bits, from LSB to MSB describing where each element belongs to (left/right). We could, of course, loop over those bits and perform 8 branches to determine which side each element belongs to, but that would be a terrible mistake. Instead, we’re going to use the `mask` as an *index* into a lookup-table for permutation values!  
  This is one of the reasons it was critical for us to have the `MoveMask` intrinsic in the first place. Without it, we would not have a scalar value we could use as an index to our table. Pretty neat, no?  
  Now, with the permutation operation done, we’ve grouped all the *smaller-or-equal* than values on one side of our `dataVec` SIMD vector/register (the "left" side) and all the *greater-than* values on the other side (the "right" side).  
  I’ve comfortably glanced over the actual values in the permutation lookup-table which `PermTablePtr` is pointing to; I'll address this a couple of paragraphs below.
* Partitioning itself is now practically complete: That is, our `dataVec` vector is already partitioned by now. Except that that data is still "stuck" inside our vector. We need to write its contents back to memory. Here comes a small complication: Our `dataVec` vector now contains *both* values that belong to the left and right portion of the original array. We did separate them **within** the vector, but we're not done until each side is written back to memory, on both ends of our array/partition.  
* <span class="uk-label">L11-12</span>: Store the same vector to both sides of the array. There is no cheap way to write *portions* of a vector to its respective end, so we write the **entire** partitioned vector to both the *left* **and** *right* sides of the array.  
  At any given moment, we have two write pointers pointing to where we need to write to **next** on either side: `writeLeft` and `writeRight`. Again, how those are initialized and maintained will be dealt with further down where we discuss the outer-loop, but for now, let's assume these pointers initially point to somewhere where it is safe to write at least an entire `Vector256<T>` and move on.
* <span class="uk-label">L13-15</span>: Book-keeping time: We just wrote 8 elements to each side, and each side had a trail of unwanted data tacked to it. We didn't care for it while we were writing it, because we are about to update the next write pointers in such a way that the *next* writes operations will **overwrite** the trailing data that doesn't belong to each respective side!  
  * The vector gods are smiling at us: We have the `PopCount` intrinsic to lend us a hand here. We issue `PopCount` on `mask` (again, `MoveMask` was worth its weight in gold here) and get a count of how many bits in `mask` were `1`. This accounts for how many values **inside** the vector were *greater-than* the pivot value and belong to the right side.
  * This "happens" to be the amount by which we want to *decrease* the `writeRight` pointer (`writeRight` is "advanced" by decrementing it, this may seem weird for now, but will become clearer when we discuss the outer-loop!)
  * Finally, we adjust the `writeLeft` pointer: `popCount` contains the number of `1` bits; the number of `0` bits is by definition, `8 - popCount` since `mask` had 8 bits of content in it, to begin with. This accounts for how many values in the register were *less-than-or-equal* the pivot value and grouped on the left side of the register.

This was a full 8-element wise partitioning block, and it's worth noting a thing or two about it:

* It is completely branch-less(!): We've given the CPU a nice juicy block with no need to speculate on what code gets executed next. It sure looks pretty when you consider the number of branches our scalar code would execute for the same amount of work. Don't pop a champagne bottle quite yet though, we're about to run into a wall full of thorny branches in a second, but sure feels good for now.
* If we want to execute multiple copies of this block, the main dependency from one block to the next is the mutation of the `writeLeft` and `writeRight` pointers. It's unavoidable given we set-out to perform in-place sorting (well, I couldn't avoid it, maybe you can!), but worth-while mentioning nonetheless. If you need a reminder about how dependencies can change the dynamics of efficient execution, you can read up on when I tried my best to go at it battling with [`PopCount` to run screaming fast](2018-08-20-netcoreapp3.0-intrinsics-in-real-life-pt3.md).

I thought it would be nice to wrap up the discussion of this block by showing off that the JIT is relatively well behaved in this case with the generated x64 asm:  
Anyone who has followed the C# code can use the intrinsics table from the previous post and read the assembly code without further help. Also, it is pretty clear that this is basically a 1:1 translation of C# code. Congratulations: It's 2020, and we're x86 assembly programmers again!
</div>

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

## Permutation lookup table

If you made it this far, you are owed an explanation of the permutation lookup table. Let's see what's in it:

* The table needs to have 2<sup>8</sup> elements for all possible mask values.
* Each element ultimately needs to be a `Vector256<int>` because that's what the permutation intrinsic expects from us, so 8 x 4 bytes = 32 bytes per element.
  * That's a whopping 8kb of lookup data in total (!).
* The values inside are [pre-generated](https://github.com/damageboy/VxSort/blob/dev/Test/PermutationTableTests.cs#L12) so that they would reorder the data *inside* a `Vector256<int>` according to our wishes: all values that got a corresponding `1` bit in the mask go to one side (right side), and the elements with a `0` go to the other side (left side). There's no particular required order amongst the grouped elements since we're merely partitioning around a pivot value, nothing more, nothing less.

Here are 4 sample values from the generated permutation table that I've copy-pasted so we can get a feel for it:

<div markdown="1">
<div markdown="1" class="stickemup">

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

</div>

* For `mask` values 0, 255 the entries are trivial: All `mask` bits were either `1` or `0` so there's nothing we need to do with the data, we just leave it as is, the “null” permutation vector: `[0, 1, 2, 3, 4, 5, 6, 7]` achieves just that.
* When `mask` is `0b00000111` (decimal 7), the 3 lowest bits of the `mask` are `1`, they represent elements that need to go to the right side of the vector (e.g., elements that were `> pivot`), while all other values need to go to the left (`<= pivot`). The permutation vector: `[3, 4, 5, 6, 7, 0, 1, 2]` does just that.
* The checkered bit pattern for the `mask` value `0b10101010` (decimal 170) calls to move all the even elements to one side and the odd elements to the other... You can see that `[0, 2, 4, 6, 1, 3, 5, 7]` does the work here.

<span class="uk-label">Note</span> that I'm technically lying here: If you look at the [actual code](https://github.com/damageboy/QuicksortAvaganza/blob/master/VxSort/PermutationTables.cs), you'll see the table's type is `ReadOnlySpan<byte>` and that the values are encoded as individual bytes with little-endian encoding. It is a CoreCLR / C# 7.3 specific optimization that allows us to treat the address of this table as a constant at JIT time. Kevin Jones ([@vcsjones](https://twitter.com/vcsjones)) did a wonderful job of digging into it, go [read his excellent post](https://vcsjones.dev/2019/02/01/csharp-readonly-span-bytes-static/) about this if you want to see interesting bits.  
We **must** use a `ReadOnlySpan<byte>` for the optimization to trigger (Not reading *that* fine-print cost me two nights of my life chasing what I was *sure* had to be a GC/JIT bug…). Normally, it would be a **bad** idea to store a `ReadOnlySpan<int>` as a `ReadOnlySpan<byte>`: we are forced to choose between little/big-endian encoding *at compile-time*. This runs up against the fact that in C# we compile once and debug (and occasionally run :) everywhere. Therefore, we have to *assume* our binaries might run on both little/big-endian machines where the CPU might not match the encoding we chose. Not great.  
**In this case**, praise the vector deities, blessed be their name and all that they touch, this is a *non-issue*: The entire premise is **x86** specific. This means that this code will **never** run on a big-endian machine. We can simply assume little endianness here till the end of all times.
{: .notice--warning}

</div>

We've covered the basic layout of the permutation table. We'll go back to it once we start optimization efforts in earnest on the 4<sup>th</sup> post, but for now, we can move on to the loop surrounding our vectorized partition block.

## Double Pumped Loop

Armed with a vectorized partitioning block, it's time to hammer our unsorted array with it, but there's a wrinkle: In-place sorting. This brings a new challenge to the table: If you followed the previous section carefully, you might have noticed it already. For every `Vector256<int>` we read, we ended up writing that same vector twice to both ends of the array. You don't have to be a math wizard to figure out that if we end up writing 16 elements for every 8 we read, that doesn't sound very in-placy, to begin with. Moreover, it would seem that this extra writing would have to overwrite data that we have not read yet. Initially, it would seem, we just placed ourselves between a rock and a hard place.

But all is not lost! In reality, we immediately adjust the next write positions on both sides in such a way that their **sum** advances by 8. In other words, we are at risk of overwriting unread data only temporarily while we store the data back. I ended up adopting a tricky approach: We will need to continuously make sure we have at least 8 elements (the size of our block) of free space on both sides of the array so we could, in turn, perform a full, efficient 8-element write to both ends without overwriting a single bit of data we haven't read yet.

Here's a visual representation of the mental model I was in while debugging/making this work (I'll note I had the same facial expressions as this poor Charizard while writing and debugging that code):

<video controls playsinline loop preload="auto" width="100%">
    <source src="../talks/intrinsics-sorting-2019/fire.webm" type="video/webm">
    <source src="../talks/intrinsics-sorting-2019/fire.mp4" type="video/mp4">
    <img src="../talks/intrinsics-sorting-2019/fire.gif " alt="">
</video>

<br/>

Funny, right? It's closer to what I actually do than I'd like to admit! I fondly named this approach in my code as "double-pumped partitioning”. It pumps values in-to/out-of **both** ends of the array at all times. I've left it pretty much intact in the repo under the name [`DoublePumpNaive`](https://github.com/damageboy/QuicksortAvaganza/blob/master/VxSort/DoublePumpNaive.cs), in case you want to dig through the full code. Like all good things in life, it comes in 3-parts:

* Prime the pump (make some initial room inside the array).
* Loop over the data in 8-element chunks executing our vectorized code block.
* Finally, go over the last remaining data elements (e.g. the last remaining `< 8` block of unpartitioned data) and partition them using scalar code. This is a very common and unfortunate pattern we find in vectorized code, as we need to finish off with just a bit of scalar work.

Let's start with another visual aid for how I ended up doing this; note the different color codes and legend I've provided here, and try to watch a few loops of this noticing the various color transitions, this will become useful for parsing the text and code below:

<div markdown="1">
<div markdown="1" class="stickemup">
<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/double-pumped-loop-with-hint.svg"></object>
</div>
<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-sorting-2019/double-pumped-loop-legend.svg"></object>

* Each rectangle is 8-elements wide.
  * Except for the middle one, which represents the last group of up to 8 elements that need to be partitioned. This is often called in vectorized parlance the "remainder problem".
* We want to partition the entire array, in-place, or turn it from <span style="padding: 1px; border: 1px solid black; border-radius: 2px; background-color: #db9d00ff">Orange</span> into the green/red colors:
  * <span style="padding: 1px; border: 1px solid black; border-radius: 2px; background-color: #bbe33d">Green</span>: for smaller-than-or-equal to the pivot values, on the left side.
  * <span style="padding: 1px; border: 1px solid black; border-radius: 2px; background-color: #c9211e; color: white">Red</span>: for greater-than-or-equal the pivot values, on the right side.
* Initially we “prime the pump”, or make some room inside the array, by partitioning into some temporary memory, marked as the 3x8-element blocks in <span style="padding: 1px; border: 1px solid black; border-radius: 2px; background-color: #f67eec">Purple</span>:
  * We allocate this temporary space, using `stackalloc` in C#, We'll discuss why this isn't really a big deal below.
  * We read one vector's worth of elements from the left and execute our partitioning block into the temporary space.
  * We repeat the process for the right side.
  * At this stage, one vector on each edge has already been partitioned, and their color is now <span style="padding: 1px; border: 1px solid black; border-radius: 2px; background-color:#b2b2b2ff">Gray</span>, which represents data/area within our array we can freely *write* into.
* From here-on, we're in the main loop: this could go on for millions of iterations, even though in this animation we only see 4 iterations in total:  
  * In every round, we *choose* where we read from next: From the left *-or-* right side of the orange area?  
    How? Easy-peasy: Whichever side has a **smaller** gray area!
    * *Intuition*: The gray area represents the distance between the head (read) and tail (write) pointers we set up for each side, the smaller the distance/area is, the more likely that our next 8-element partition *might* end with us overwriting that side's head with the tail.
    * **We really don't want that to happen...**
    * We read from the only side *where this might happen next*, thereby adding 8 more elements of breathing space to that side just in time before we cause a meltdown. (you can see this clearly in the animation as each orange block turns gray *after* we read it, *but before* we write to both sides...)
  * We partition the data inside the `Vector256<int>` we just read and write it to the next write position on each side.
  * We advance each write pointer according to how much of that register was red/green, we’ve discussed the how of it when we toured the vectorized block. Here you can see the end result reflected in how the red portion of the written copy on the left-hand side turns into gray, and the green portion on the right-hand side turns into gray correspondingly.  
    **Remember**: We've seen the code in detail when we previously discussed the partitioning block; I repeat it here since it is critical for understanding how the whole process clicks together.
* For the finishing touch:
  * Left with less than 8 elements, we partition with plain old scalar code the few remaining elements, into the temporary memory area again.
  * We copy back each side of the temporary area back to the main array, and we’re done!
  * We move the pivot value that was left untouched all this time on the right edge of our segment and move it to where the new boundary is.

Let's go over it again, in more detail, this time with code:
</div>

### Setup: Make some room!

What I eventually opted for was to read from *one* area and write to *another* area in the same array. But we need to make some spare room inside the array for this. How?

We cheat! (¯\\_(ツ)_/¯), but not really: we allocate some temporary space, using `stackalloc` in C#, here's why this isn't really cheating in any reasonable person’s book:

* Stack allocation doesn't put pressure on the GC, and its allocation is super fast/slim.
* We allocate *once* at the top of our entire sort operation and reuse that space while recursing.
* “Just a bit" is really just a bit: For our 8-element partition block we need room for 1 x 8-elements vector on **each** side of the array, so we allocate a total of 2 x 8 integers. In addition, we allocate 8 more elements for handling the remainder (well technically, 7 would be enough, but I'm not a monster, I like round numbers just like the next person), so a total of 96 bytes. Not too horrid.

Here's the signature + setup code:

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
unsafe int* VectorizedPartitionInPlace(int* left, int* right)
{
    var N = Vector256<T>.Count; // Treated by JIT as constant!
    var pivot = *right;
    var P = Vector256.Create(pivot);

    var tmpLeft = _tempStart;
    var tmpRight = _tempEnd - N;
    PartitionBlock(left,          P, ref tmpLeft, ref tmpRight);
    PartitionBlock(right - N - 1, P, ref tmpLeft, ref tmpRight);

    var writeLeft = left;
    var writeRight = right - N - 1;
    var readLeft  = left + N;
    var readRight = right - 2*N - 1;
```

</div>

The function accepts two parameters: `left`, `right` pointing to the edges of the partitioning task we were handed. The selected pivot is “passed” in an unconventional way: the caller (The top-level sort function) is responsible for **moving** it to the right edge of the array before calling the partitioning function. In other words, we start executing the function expecting the pivot to be already selected and placed at the right edge of the segment (e.g., `right` points to it). This is a remnant of my initial copy-pasting of CoreCLR code, and to be honest, I don't care enough to change it.

<span class="uk-label">Note</span> that I'm using a "variable" (`N`) instead of `Vector256<int>.Count`. There's a reason for those double quotes: At JIT time, the right-hand expression is considered as a constant as far as the JIT is concerned. Furthermore, once we initialize N with its value and *never* modify it, the JIT treats N as a constant as well! So really, I get to use a short/readable name and pay no penalty in for it.
{: .notice--info}

We proceed to partition a single 8-element vector on each side, with our good-ole' partitioning block **straight into** that temporary space. It is important to remember that having done that, we don't care about the original contents of the area we just read from anymore: we're free to write up to one `Vector256<T>` to each edge of the array in the future. We've made enough room inside our array available for writing in-place while partitioning. We finish the setup by initializing read and write pointers for every side (`readLeft`, `readRight`, `writeLeft`, `writeRight`); An alternative way to think about them is that each side gets its own head (read) and tail (write) pointers. We will be continuously reading from **one** of the heads and writing to **both** tails from now on.

The setup ends with `readLeft` pointing a single `Vector256<int>` *right* of `left` , and `readRight` pointing 1 element + 2x`Vector256<int>` *left* of `right`. The setup og `readRight` might initially seem peculiar, but easily explained:

* `right` itself points to the selected pivot; we're not going to (re-)partition it, so we skip that element (this explains the `- 1`).
* As with the `tmpRight` pointer, when we read/write using `Avx2.LoadDquVector256`/`Avx.Store` we always have to supply the *start* address to read from or write to!  
  Since There is no ability to read/write to the "left" of the pointer, we decrement that pointer by `2*N` to account for the data that was already partitioned and to prepare it for the next write.

### Loop

Here's the same loop we saw in the animation with our vectorized block smack in its middle, in plain-old C#:
</div>
<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
    while (readRight >= readLeft) {
        int *nextPtr;
        if ((readLeft   - writeLeft) <= (writeRight - readRight)) {
            nextPtr = readLeft;
            readLeft += N;
        } else {
            nextPtr = readRight;
            readRight -= N;
        }

        PartitionBlock(nextPtr, P, ref writeLeft, ref writeRight);
    }
    readRight += N;
    tmpRight += N;
```

</div>

This is the heart of the partitioning operation and where we spend most of the time sorting the array. Looks quite boring, eh?

This loop is all about calling our good ole' partitioning block on the entire array. We-reuse the same block, but here, for the first time, actually use it as an in-place partitioning block, since we are both reading and writing to the same array.
While the runtime of the loop is dominated by the partitioning block, the interesting bit is that beefy condition on <span class="uk-label">L3</span> that we described/animated before: it calculates the distance between each head and tail on both sides and compares them to determine which side has less space left, or which side is closer to being overwritten. Given that the **next** read will happen from the side we choose here, we've just added 8 more integers worth of *writing* space to that same endangered side, thereby eliminating the risk of overwriting.

<span class="uk-label">Note</span> that while it might be easy to read in terms of correctness or motivation, this is a very *sad line of code*, as it will haunt us in the next posts!
{: .notice--info}

Finally, as we exit the loop once there are `< 8` elements left (remember that we pre-decremented `readRight` by `N` elements before the loop), we are done with all vectorized work for this partitioning call. as such, this is as good a time to re-adjust both `readRight` and `tmpRight` that were pre-decremented by `N` elements to make them ready-to-go for using `Avx.Store` back in preparation for the final step.

### Handling the remainder and finishing up

Here's the final piece of this function:
</div>
<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
    while (readLeft < readRight) {
        var v = *readLeft++;
        if (v <= pivot) {
            *tmpLeft++ = v;
        } else {
            *--tmpRight = v;
        }
    }

    var leftTmpSize = (uint) (int) (tmpLeft - _tempStart);
    Unsafe.CopyBlockUnaligned(writeLeft, _tmpStart, leftTmpSize * sizeof(int));
    writeLeft += leftTmpSize;
    var rightTmpSize = (uint) (int) (_tempEnd - tmpRight);
    Unsafe.CopyBlockUnaligned(writeLeft, tmpRight, rightTmpSize * sizeof(int));
    Swap(writeLeft, right);
    return writeLeft;
}
```

</div>

Finally, we come out of the loop once we have less than 8-elements to partition (1-7 elements). We can't use vectorized code here, so we drop to plain-old scalar partitioning. To keep things simple, we partition these last elements straight into the temporary area. This is the reason we're allocating 8 more elements in the temporary area in the first place.

Once we're done with this remainder nuisance, we copy back the already partitioned data from the temporary area back into the array to the area left between `writeLeft` and `writeRight`, it's a quick 64-96 byte copy in two operations, and we are nearly done. We still need to move the pivot *back* to the newly calculated pivot position (remember the caller placed it on the right edge of the array as part of pivot selection) and report this position back as the return value for this to be officially be christened as AVX2 partitioning function.
</div>

## Pretending we're Array.Sort

Now that we have a proper partitioning function, it's time to string it into a quick-sort like dispatching function: This will be the entry point to our sort routine:

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp
public static class DoublePumpNaive
{
    public static unsafe void Sort<T>(T[] array) where T : unmanaged, IComparable<T>
    {
        if (array == null)
            throw new ArgumentNullException(nameof(array));

        fixed (T* p = &array[0]) {
            if (typeof(T) == typeof(int)) {
                var pInt = (int*) p;
                var sorter = new VxSortInt32(startPtr: pInt, endPtr: pInt + array.Length - 1);
                sorter.Sort(pInt, pInt + array.Length - 1);
            }
        }
    }

    const int SLACK_PER_SIDE_IN_VECTORS = 1;
```

</div>

Most of this is pretty boring code:

* We start with a top-level static class `DoublePumpNaive` containing a single `Sort` entry point accepting a normal managed array.
* We special case, relying on generic type elision, for  `typeof(int)`, newing up a `VxSortInt32` struct and finally calling its internal `.Sort()` method to initiate the recursive sorting.
  * This is a good time as any to remind, again, that for the time being, I only implemented vectorized sorting when `T` is `int`. To fully replace `Array.Sort()` more tweaked versions of this code will have to be written to eventually support unsigned integers, both larger and smaller than 32 bits as well as floating-point types.

Continuing on to `VxSortInt32` itself:

</div>

<div markdown="1">
<div markdown="1" class="stickemup">

```csharp

    internal unsafe struct VxSortInt32
    {
        const int SLACK_PER_SIDE_IN_ELEMENTS    = SLACK_PER_SIDE_IN_VECTORS * 8;
        const int TMP_SIZE_IN_ELEMENTS          = 2 * SLACK_PER_SIDE_IN_ELEMENTS + 8;
        const int SMALL_SORT_THRESHOLD_ELEMENTS = 16;

        readonly int* _startPtr;
        readonly int* _endPtr;
        readonly int* _tempStart;
        readonly int* _tempEnd;
        fixed int _temp[TMP_SIZE_IN_ELEMENTS];

        public VxSortInt32(int* startPtr, int* endPtr) : this()
        {
            _startPtr = startPtr;
            _endPtr   = endPtr;
            fixed (int* pTemp = _temp) {
                _tempStart = pTemp;
                _tempEnd   = pTemp + TMP_SIZE_IN_ELEMENTS;
            }
        }
```
</div>

This is where the real top-level sorting entry point for 32-bit igned integers is:

* This struct contains a bunch of constants and members that are initialized for a single sort-job/call and immediately discarded once sorting is completed.
* There's a little nasty bit hiding in plain-sight there, where we exfiltrtrate an interior pointer obtained inside a `fixed` block and store it for the lifetime of the struct, outside of the `fixed` block.
  * This is generally a no-no since in theory we don't have a guarantee that the struct won't be boxed / stored inside a managed object on a heap where the GC is free to move our struct around.
  * In this case, it is assumed that caller is internal and will only ever call `new VxSortInt32(...)` in the context of stack allocation and keep it that way.
  * This somewhat abnormal assumption was taken so that the temporary memory sits close to the various pointers, taking advantage of locality of reference.

</div>

```csharp
        internal void Sort(int* left, int* right)
        {
            var length = (int) (right - left + 1);

            int* mid;
            switch (length) {
                case 0:
                case 1:
                    return;
                case 2:
                    SwapIfGreater(left, right);
                    return;
                case 3:
                    mid = right - 1;
                    SwapIfGreater(left, mid);
                    SwapIfGreater(left, right);
                    SwapIfGreater(mid,  right);
                    return;
            }

            // Go to insertion sort below this threshold
            if (length <= SMALL_SORT_THRESHOLD_ELEMENTS) {
                InsertionSort(left, right);
                return;
            }

            // Compute median-of-three, of:
            // the first, mid and one before last elements
            mid = left + ((right - left) / 2);
            SwapIfGreater(left, mid);
            SwapIfGreater(left, right - 1);
            SwapIfGreater(mid,  right - 1);

            // Pivot is mid, place it in the right hand side
            Swap(mid, right);

            var boundary = VectorizedPartitionInPlace(left, right);

            Sort( left, boundary - 1);
            Sort(boundary + 1,  right);
        }
```

Lastly, we have the `Sort` method for the `VxSortInt32` struct. Most of this is code I blatantly copied for [`ArraySortHelper<T>`](https://github.com/dotnet/coreclr/blob/master/src/System.Private.CoreLib/shared/System/Collections/Generic/ArraySortHelper.cs#L182). What it does is:

* Special case for lengths of 0-3
* When length `<= 16` we just go straight to `InsertionSort` and skip all the recursive jazz (go back to post 1 if you want to know why `Array.Sort()` does this).
* When we have `>= 17` elements, we go to vectorized partitioning:
  * we do median of 3 pivot selection
  * swap that pivot so that it resides on the right-most index of the partition.
* Call `VectorizedPartitionInPlace`, which we've seen before.
  * We conveniently take advantage of the fact we have `InsertionSort` to cover us for the small partitions, and our partitioning code can always assume that it can do at least two vectors worth of vectorized partitioning without additional checks...
* Recurse to the left.
* Recurse to the right.

## Initial Performance

Are we fast yet?

Yes! This is by no means the end, on the contrary, this is only a rather impressive beginning. We finally have something working, and it is even not entirely unpleasant, if I may say so:

<div markdown="1">
<div class="stickemup">

{% codetabs %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Scaling %}
<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Performance scale: Array.Sort (solid gray) is always 100%, and the other methods are scaled relative to it" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<canvas height="130vmx" data-chart="line">
N,100,1K,10K,100K,1M,10M
ArraySort,         1   , 1   , 1  , 1   , 1    , 1
DoublePumpedNaive, 1.05, 0.87, 0.6, 0.58, 0.39 , 0.37
<!-- 
{ 
 "data" : {
  "datasets" : [ { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3	}
  },
  { 
    "backgroundColor": "rgba(33,33,220,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 30, "hachureGap": 6	}
  }]
 },
 "options": {
    "title": { "text": "AVX2 Naive Sorting - Scaled to Array.Sort", "display": true },
    "scales": { 
      "yAxes": [{ 
        "ticks": { "min": 0.2, "callback": "ticksPercent" },
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

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-stats'></i> Time/N %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>
<div data-intro="Size of the sorting problem, 10..10,000,000 in powers of 10" data-position="bottom">
<div data-intro="Time in nanoseconds spent sorting per element. Array.Sort (solid gray) is the baseline, again" data-position="left">
<div data-intro="Click legend items to show/hide series" data-position="right">
<canvas height="130vmx" data-chart="line">
N,100,1K,10K,100K,1M,10M
ArraySort        , 19.2578, 29.489 , 53.9452, 60.0894, 69.4293, 80.4822
DoublePumpedNaive, 16.7518, 25.7378, 32.6388, 34.5511, 27.2976, 29.5117
<!-- 
{ 
 "data" : {
  "datasets" : [ { 
    "backgroundColor": "rgba(66,66,66,0.35)",
    "rough": { "fillStyle": "hachure", "hachureAngle": -30, "hachureGap": 9, "fillWeight": 0.3	}
  },
  { 
    "backgroundColor": "rgba(33,33,220,.9)",
    "rough": { "fillStyle": "hachure", "hachureAngle": 30, "hachureGap": 6	}
  }]
 },
 "options": {
    "title": { "text": "AVX2 Naive Sorting - log(Time/N)", "display": true },
    "scales": { 
      "yAxes": [{ 
        "type": "logarithmic",
        "ticks": {
          "callback": "ticksNumStandaard"
        },
        "scaleLabel": {
          "labelString": "Time/N (ns)",
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
{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-list-alt'></i> Benchmarks %}

<div markdown="1">
<button name="button" class="uk-button uk-button-small uk-button-primary uk-align-center uk-width-5-6" data-toggle="chardinjs" onclick="$('body').chardinJs('start')">Tell me what I'm seeing here</button>

| Method                | N        |   Mean (µs) | Time / N (ns) | Ratio |
| --------------------- | -------- | ----------: | ------------: | ----: |
| ArraySort             | 100      |       1.926 |       19.2578 |  1.00 |
| AVX2DoublePumpedNaive | 100      |       1.675 |       16.7518 |  1.05 |
| ArraySort             | 1000     |      29.489 |       29.4890 |  1.00 |
| AVX2DoublePumpedNaive | 1000     |      25.738 |       25.7378 |  0.87 |
| ArraySort             | 10000    |     539.452 |       53.9452 |  1.00 |
| AVX2DoublePumpedNaive | 10000    |     326.388 |       32.6388 |  0.60 |
| ArraySort             | 100000   |   6,008.936 |       60.0894 |  1.00 |
| AVX2DoublePumpedNaive | 100000   |   3,455.113 |       34.5511 |  0.58 |
| ArraySort             | 1000000  |  69,429.272 |       69.4293 |  1.00 |
| AVX2DoublePumpedNaive | 1000000  |  27,297.568 |       27.2976 |  0.39 |
| ArraySort             | 10000000 | 804,821.776 |       80.4822 |  1.00 |
| AVX2DoublePumpedNaive | 10000000 | 295,116.936 |       29.5117 |  0.37 |

</div>

{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-list-alt'></i> Stats %}

<div>
<button class="helpbutton" data-toggle="chardinjs" onclick="$('body').chardinJs('start')"><object style="pointer-events: none;" type="image/svg+xml" data="/assets/images/help.svg"></object></button>

<table class="table datatable"
  data-json="../_posts/unmanaged-vs-doublepumpednaive-stats.json"
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
{% endcodetab %}

{% codetab <i class='glyphicon glyphicon-info-sign'></i> Setup %}

```bash
BenchmarkDotNet=v0.11.5, OS=clear-linux-os 30850
Intel Core i7-7700HQ CPU 2.80GHz (Kaby Lake), 1 CPU, 4 logical and 4 physical cores
.NET Core SDK=3.0.100-rc1-014015
  [Host]     : .NET Core 3.0.0-rc1-19425-03 (CoreCLR 4.700.19.42204, CoreFX 4.700.19.42010), 64bit RyuJIT
  Job-PDGVYD : .NET Core 3.0.0-rc1-19425-03 (CoreCLR 4.700.19.42204, CoreFX 4.700.19.42010), 64bit RyuJIT

InvocationCount=10  IterationCount=3  LaunchCount=1
UnrollFactor=1  WarmupCount=3
```

{% endcodetab %}

{% endcodetabs %}
</div>

We're off to a very good start:

* We can see that as soon as we hit 1000 element arrays (even earlier, in earnest), we already outperform `Array.Sort` (87% runtime), and by the time we get to 1M / 10M element arrays, we see speed-ups north of 2.5x (39%, 37% runtime) over the scalar C++ code!  

* While `Array.Sort` is behaving like we would expect from a `QuickSort`-like function: it is slowing down at rate you'd expect given that it has a Big-O notation of $$\mathcal{O}(n\log{}n)$$, our own `DoublePumpedNaive` is peculiar: The time spent sorting every single element starts going up as we increase `N`, then goes down a bit and back up. Huh? It actually improves as we sort more data? Quite unreasonable, unless we remind ourselves that we are executing a mix of scalar insertion sort and vectorized code. Where are we actually spending more CPU cycles though?  We'll run some profiling sessions in a minute, to get a better idea of what's going on.

If you recall, on the first post in this series, I presented some statistics about is going on inside our sort routine. This is a perfect time to switch to the statistics tab, where I've beefed up the table with some vectorized counters that didn't make sense before with the scalar version. From here we can learn a few interesting facts:

* The number of partitioning operations / small sorts is practically the same
  * You could ask yourself, or me, why they are not **exactly** the same?
    To which I'd answer:
    * The thresholds are 16 vs. 17, which has some effect.
    * We have to remember that the resulting partitions from each implementation end up looking slightly different because of the double pumping + temporary memory shenanigans. Once the partitions look different, the following pivots selected are different, and the entire whole sort mechanic looks slightly different.
* We are doing a lot of vectorized work:
  * Loading two vectors per 8-element(1 data vector + 1 permutation vector)
  * Storing two vectors (left+right) for every vector read
  * In a weird coincidence, this means we have perform the same number of vectorized loads and store for every test case.
  * Finally, lest we forget, we perfom compares/permutations at exactly half of the load/store rate.
* All of this is helping us by reducing the number of scalar comparisons, but there's still quite a lot of it left too:
  * We continue to do scalar partitioning inside `VectorizedPartitionInPlace`, as part of handling the remainder that doesn't fit into a `Vector256<int>`.
  * We are executing tons of scalar comparisons inside of the insertion sort.  
    It is clear that the majority of scalar comparisons are now coming from the `InsertionSort`: If we focus on the 1M/10M cases here, we see that we went up from attributing 28.08%/24.60% of scalar comparisons in the `Unmanaged` (scalar) test-case all the way to 66.4%/62.74% in the vectorized `DoublePumpNaive` version. This is simply due to the fact that the vectorized version executes less data-dependent branches.

This is but the beginning of our profiling journey, but we are already learning a complicated truth: Right now, as fast as this is already going, the scalar code we use for insertion sort will always put an upper limit on how fast we can possibly go by optimizing the *vectorized code* we've gone over so far, *unless* we get rid of `InsertionSort` alltogether, replacing it with something better. But first thing's first, we must remain focused: 65% of instructions executed are still spent doing vectorized partitioning; That is the biggest target on our scope!
</div>

As promised, it's time we profile the code to see what's really up: We can fire up the venerable Linux `perf` tool, through a simple test binary/project I've coded up which allows me to execute some dummy sorting by selecting the sort method I want to invoke and specify some parameters for it through the command line, for example:

```bash
$ cd ~/projects/public/VxSort/Example
$ dotnet publish -c release -o linux-x64 -r linux-x64
# Run AVX2DoublePumped with 1,000,000 elements x 100 times
$ ./linux-x64/Example --type-list DoublePumpedNaive --sizes 1000000
```

Here we call the `DoublePumpedNaive` implementation we've been discussing from the beginning of this post with 1M elements, and sort the random data 100 times to generate some heat in case global warming is not cutting it for you.  
I know that calling `dotnet publish ...`  seems superfluous, but just trust[^0] me and go with me on this one:

{% codetabs %}

{% codetab 1M %}

```bash
$ COMPlus_PerfMapEnabled=1  perf record -F max -e instructions ./Example \
       --type-list DoublePumpedNaive --sizes 1000000
...
$ perf report --stdio -F overhead,sym | head -15
...
# Overhead  Symbol
    65.66%  [.] ... ::VectorizedPartitionInPlace(int32*,int32*,int32*)[Optimized]
    22.43%  [.] ... ::InsertionSort(!!0*,!!0*)[Optimized]
     5.43%  [.] ... ::QuickSortInt(int32*,int32*,int32*,int32*)[OptimizedTier1]
     4.00%  [.] ... ::Memmove(uint8&,uint8&,uint64)[OptimizedTier1]
```

{% endcodetab %}

{% codetab 10K %}

```bash
$ COMPlus_PerfMapEnabled=1 perf record -F max -e instructions ./Example \
       --type AVX2DoublePumpedNaive --sizes 10000
...
$ perf report --stdio -F overhead,sym | head -15
...
# Overhead  Symbol
    54.59%  [.] ... ::VectorizedPartitionInPlace(int32*,int32*,int32*)[Optimized]
    29.87%  [.] ... ::InsertionSort(!!0*,!!0*)[Optimized]
     7.02%  [.] ... ::QuickSortInt(int32*,int32*,int32*,int32*)[OptimizedTier1]
     5.23%  [.] ... ::Memmove(uint8&,uint8&,uint64)[OptimizedTier1]
```

{% endcodetab %}

{% endcodetabs %}

This is a trimmed summary of `perf` session recording performance metrics, specifically: number of instructions executed for running a 1M element sort 100 times, followed by running a 10K element sort, 10K times. I was initially shocked when I saw this for the first time, but we're starting to understand the previous oddities we saw with the `Time/N` column!  
We're spending upwards of 20% of the our time doing scalar insertion sorting! This was supposed to be vectorized sorting and yet, somehow, "only" 65% of the time is spent in the vectorized function (which also has some scalar parts, to be frank). Not only that, but as the size of the array decreases, the percentage of time spent in scalar code *increases* (from 22.43% to 29.87%), which should not surprise us anymore.  
Before anything else, let me clearly state that this is not necessarily a bad thing! As the size of the partition decreases, the *benefit* of doing vectorized partitioning decreases in general, and even more so for our AVX2 partitioning, which has non-trivial start-up overhead. We shouldn't care about the amount of time we're spending on scalar code per se, but the amount of time taken to sort the entire array.  
The decision to go to scalar insertion sort or stick to our vectorized code is controlled by the threshold I mentioned before, which is sitting there at `16`. We're only beginning our optimization phase in the next post, so for now, we'll stick with the threshold selected for `Array.Sort` by the CoreCLR developers, but this is definitely something we will tweak later for our particular implementation.

## Finishing off with a sour taste

I’ll end this post with a not so easy pill to swallow: let's re-run `perf` and see how the code is behaving in terms of top-level performance counters. The idea here is to use counters that our CPU is already capable of collecting at the hardware level, with almost no performance impact, to see where/if we’re hurting. What I'll do before invoking `perf` is use a Linux utility called [`cset`](https://github.com/lpechacek/cpuset) which can be [used to](https://stackoverflow.com/a/13076880/9172) evacuate all user threads and (almost all) kernel threads from a given physical CPU core, using [cpusets]( https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cgroup-v1/cpusets.rst):

```bash
$ sudo cset shield --cpu 3 -k on
cset: --> activating shielding:
cset: moving 638 tasks from root into system cpuset...
[==================================================]%
cset: kthread shield activated, moving 56 tasks into system cpuset...
[==================================================]%
cset: **> 38 tasks are not movable, impossible to move
cset: "system" cpuset of CPUSPEC(0-2) with 667 tasks running
cset: "user" cpuset of CPUSPEC(3) with 0 tasks running
```

Once we have “shielded” a single CPU core, we execute the `Example` binary we used before much in the same way while collecting different top-level hardware statistics from befre using a the following `perf` command line:

```bash
$ perf stat -a --topdown sudo cset shield -e \
    ./Example --type-list DoublePumpedNaive --sizes 1000000 --no-check
cset: --> last message, executed args into cpuset "/user", new pid is: 16107

 Performance counter stats for 'system wide':
        retiring      bad speculation       frontend bound        backend bound
...
...
...
S0-C3 1    37.6%                32.3%                16.9%                13.2%

       3.221968791 seconds time elapsed

```

I'm purposely showing only the statistics collected for our shielded core since we know we only care about that core in the first place.

Here are some bad news: core #3 is really not having a good time running our code with 32.3% of the branches taken being mis-speculated. This might seem like an innocent statistic if you haven't done this sort of thing before (in which case, read the info box below), but this is **really bad**. The penalty for *each* bad speculation is an entire flush of the pipeline, which costs us around 14-15 cycles on modern Intel CPUs.

<span uk-icon="icon: info; ratio: 2"></span>  
We have to remember that efficient execution on modern CPUs means keeping the CPU pipeline as busy as possible; This is quite a challenge given its length is about 15 stages, and the CPU itself is super-scalar (e.g., it can execute up to 3-4 instructions each cycle!). If, for example, all instructions in the CPU have a constant latency in cycles, this means it *has* to process 100+ instructions into "the future" while it's just finishing up with a current one to avoid doing nothing. That's enough of a challenge for regular code, but what should it do when it sees a branch? It could attempt and execute **both** branches, which quickly becomes a fool's errand if somewhere close-by there would be even more branches. What CPU designers did was opt for speculative execution: add complex machinery to *predict* if a branch will be taken and speculatively execute the next instruction according to the prediction. But the predictor isn't all knowing, and it can still mis-predict, and then we end up paying a huge penalty for *unwinding* all of the instructions that began execution in a mis-predicted path.
{: .notice--info}

Wait, I sense your optimistic thoughts all across the internet... maybe it's not our vectorized so-called branch-less code? Maybe we can chalk it all up on that mean scalar `InsertionSort` function doing those millions and millions of scalar comparisons? We are, after all, using it for sorting small partitions, which we've already measured at more than 20% of the total run-time? Let's see this again with `perf`, *this time* focusing on the `branch-misses` HW counter and try to figure out how the mis-predictions are distributed amongst our call-stacks:

```bash
$ export COMPlus_PerfMapEnabled=1 # Make perf speak to the JIT
# Record some performance information:
$ perf record -F max -e branch-misses --type-list DoublePumpedNaive --sizes 1000000 --no-check
...
$ perf report --stdio -F overhead,sym | head -17
...
    40.97%  [.] ...::InsertionSort(!!0*,!!0*)[Optimized]
    32.30%  [.] ...::VectorizedPartitionInPlace(int32*,int32*,int32*)[Optimized]
     9.64%  [.] ...::Memmove(uint8&,uint8&,uint64)[OptimizedTier1]
     9.64%  [.] ...::QuickSortInt(int32*,int32*,int32*,int32*)[OptimizedTier1]
     5.62%  [.] ...::VectorizedPartitionOnStack(int32*,int32*,int32*)[Optimized]
...
```

No such luck. While `InsertionSort` is definitely starring here with 41% *of the* bad speculation events, we still have **32%** of the bad speculation coming from our own new vectorized code. This means that our vectorized code still contains a lot of data-dependent branches. The resulting pipeline flush is a large penalty to pay given that our entire 8-element partition block has a throughput of around 8-9 cycles. That means we are hitting that 15 cycle pan-to-the-face too often to feel good about ourselves.

I'll finish this post here. We have a **lot of work** cut out for us. This is no-where near over.  
In the [next post]({% post_url 2019-08-21-this-goes-to-eleven-pt4 %}), I'll try to give the current vectorized code a good shakeup. After all, it's still our biggest target in terms of number of instructions executed, and 2<sup>nd</sup> when it comes to branch mis-predictions. Once we finish squeezing that lemon for all its performance juice on the 4<sup>th</sup> post, We will turn our focus to the `InsertionSort` function on the 5<sup>th</sup> post , and we'll see if we can appease the performance gods to make that part of the sorting effort faster.  
In the meantime, you can try and go back to the vectorized partitioning function and try to figure out what is causing all those nasty branch mis-predictions if you're up for a small challenge. We'll be dealing with it at the end of the [next post]({% post_url 2019-08-21-this-goes-to-eleven-pt4 %}).

----

[^0]: For some, `perf` wasn't in the mood to show me function names without calling `dotnet publish`  and using the resulting binary, and I didn't care enough to investigate further...
