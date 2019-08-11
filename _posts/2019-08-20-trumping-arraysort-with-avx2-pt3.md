---
title: "Trumping Array.Sort with AVX2 Intrinsics (Part 3/6)"
header:
  image: /assets/images/coreclr-clion-header.jpg
hidden: true
date: 2019-08-20 11:26:28 +0300
classes: wide
#categories: coreclr intrinsics vectorization quicksort sorting
---

I ended up going down the rabbit hole re-implementing `Array.Sort()` with AVX2 intrinsics, and there’s no reason I should store all the agony inside (to be honest: I had a lot of fun with this). I should probably attempt to have a serious discussion with CoreCLR  / CoreFX people about starting a slow process that would end with integrating this code into the main C# repos, but for now, let's get in the ring and show what AVX/AVX2 intrinsics can really do for a non-trivial problem, and even discuss potential improvements that future CoreCLR versions could bring to the table.

Since there’s a lot to over go over here, I’ll split it up into a few parts:

1. In [part 1]({% post_url 2019-08-18-trumping-arraysort-with-avx2-pt1 %}), we did a short refresher on `QuickSort` and how it compares to `Array.Sort()`. If you don’t need any refresher, you can skip over it and get right down to part 2 and onwards , although I really recommend skimming through, mostly because I’ve got really good visualizations for that should be in the back of everyone’s mind as we’ll be dealing with vectorization & optimization later.
2. In [part 2]({% post_url 2019-08-19-trumping-arraysort-with-avx2-pt2 %}), we go over the basics of Vectorized HW Intrinsics, discussed vector types, and a handful of vectorized instructions we’ll actually be using in part 3, but we still weren't sorting anything.
3. In this part, we go through the initial code for the vectorized sorting and we’ll finally start seeing some payoff. We’ll finish with some agony courtesy of CPU’s Branch Predictor, just so we don't get too cocky.
4. In [part 4]({% post_url 2019-08-21-trumping-arraysort-with-avx2-pt4 %}), we go over a handful of optimization approaches that I attempted trying to get the vectorized partition to run faster, we'll see what worked and what didn't.
5. In part 5, we’ll see how we can almost get rid of 100% of the remaining scalar code, by implementing small-constant size array sorting. We’ll use, drum roll…, yet more AVX2 vectorization and gain a considerable amount of performance / efficiency in the process.
6. Finally, in part 6, I’ll list the outstanding stuff / ideas I have for getting more juice and functionality out of my vectorized code.

## Vectorized Partitioning + QuickSort

It’s time we mash all the new knowledge we picked up in the last post about SIMD registers and instructions and do something useful with them. Here's the plan:

* First we take 8-element blocks, or units of `Vector256<int>`, and partition them with AVX2 intrinsics.
* Then we take the world: We reuse our block to partition an entire array by wrapping it with code that:
  * Preps the array
  * Loops over the data in 8-element chunks running our vectorized code block
  * Goes over the rest of the data and partitions the remainder using scalar code, since we're all out of 8-elements chunks, we need to finish off with just a bit of scalar partitioning, this is unfortunate but very typical for any vectorized code in the wild.
* Once we have vectorized partitioning, we'll cover the specifics of how that code is invoked from the top-level sorting entry point.

### AVX2 Partitioning Block

 Let’s start with this “simple” block:

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
  We’re just creating 8-copies of the selected pivot value in our `P` value/register.
  
  It is important to remember that this happens only *once* per partitioning function call!
  {: .notice--info}
  
* Next in line 3:  
  ```csharp
  var current = Avx2.LoadDquVector256(nextPtr);
  ```
  We load up the data from somewhere (`nextPtr`) in our array. We’ll focus on where `nextPtr` points to later, but for now we can go forward, we have data we need to partition, and that's the important bit.

* Then comes an 8-way comparison using `CompareGreaterThan` & `MoveMask` call in lines 4-5:  
  ```csharp
  var mask = (uint) Avx.MoveMask(
    Avx2.CompareGreaterThan(current, P).AsSingle()));
  ```
  This ultimately generates a **scalar** `mask` value which will contain `1` bits for every comparison where the respective data element was greater-than the pivot value, and `0` bits for all other elements. If you are having a hard time following *why* this does this, you need to head back to the [2<sup>nd</sup> post](2019-08-19-trumping-arraysort-with-avx2-pt2.md) and read up on these two intrinsics / watch the respective animations…

* In lines 6-7 we permute the loaded data according to a permutation value:  
  
  ````csharp
  current = Avx2.PermuteVar8x32(current,
      LoadDquVector256(PermTablePtr + mask * 8));
  ````
  
  Here comes a small surprise! We’re going to use the `mask` as an **index** into a lookup-table for permutation values! Bet you didn't see that one coming...  
  This is one reason it was critical for us to have the `MoveMask` intrinsic in the first place! Without it we would not have `mask` as a scalar value/register, and wouldn’t be able to use it as an index to our table. Pretty neat, no?    
  After the permutation operation is done, we’ve grouped all the *smaller-or-equal* than values on one side of our `current` SIMD vector/register (let’s call it the left side) and all the *greater-than* values on the other side (right side).  
  I’ve comfortably glanced over the actual values in the permutation lookup-table which `PermTablePtr` is pointing to; worry not, it’s just a couple of paragraphs down.
  
* In case this wasn’t abundantly clear, the partitioning operation is now complete. That is, our `current` SIMD value/register is already partitioned by line 8, except that we need to write the partitioned values back to memory. Here comes a small complication: Our `current` value now contains both values that are *smaller-or-equal* than the pivot and *greater-than*. We did separate them **within** the SIMD register together on both "sides" of the register, but we're not done until this is reflected all the way in memory.  
  What I ended up doing was to write the **entire** partitioned vector to both the *left* **and** *right* sides of the array!  
  At any given moment, we have two write pointers pointing to where we need to write to **next** on either side: `writeLeft` and `writeRight`. Again, how those are initialized and maintained will be dealt with further down this post where we discuss the outer-loop, but for now lets assume these pointers initially point to somewhere where it’s safe to write at least an entire single 256 bit SIMD register, and move on. In lines 8,9 we just store the entire partition SIMD register to **both** sides in two calls:
  
  ```csharp
  Avx.Store(writeLeft, current);
  Avx.Store(writeRight, current);
  ```
  
* We just wrote 8 elements to each side, but in reality the partitioned register had a mix of values: some were destined to the left side of the array, and some to the right. We didn't care for it while we were writing, but we need to make sure the *next* write pointers are adjusted according to how the values were partitioned inside the register…  
  Well, the vector gods are smiling at us: we have the `PopCount` intrinsic to lend us a hand here. On line 10, we `PopCount` the mask value (again, `MoveMask` was worth its weight in gold here) and get a count of how many bits in the mask value were `1`. Remember that this count directly corresponds to how many values **inside** the SIMD register were greater-than the pivot value and are now grouped on the right, which just happens to exactly be the amount by which we want to *decrease* the `writeRight` pointer on line 11:
  
  ```csharp
  var popCount = PopCnt.PopCount(mask);
  writeRight -= popCount;
  ```
  
  *Note*: The `writeRight` pointer is "advanced" by decrementing it, this might seem weird for now, but will become clearer when we discuss the outer-loop!
  {: .notice--info}
  
* And finally, since we know that there were exactly 8 elements, and that the `popCount` value contains the number of `1` bits; the number of `0` bits is by definition `8 - popCount` since `mask` only had 8 bits to data in it to begin with, which is really the count of how many values in the register where *less-than-or-equal* the pivot value and grouped on the left side of the register.  So we advance the `writeLeft` pointer on line 12:

  ```csharp
  writeLeft  += 8 - popCount;
  ```

And we’re done!

This was a full 8-element wise partitioning block, and it's worth noting a thing or two about it:

* It is completely branch-less(!): We've given the CPU a nice juicy loop body with no need to speculate on what code gets executed next. It sure looks pretty when you consider the amount of branches our scalar code would do for the same amount of work. Don't celebrate yet though, we're about to run into a wall in a second, but sure feels good for now.
* Once this goes inside a loop, the only dependency between *different iterations* of this code is the mutation of the `writeLeft` and `writeRight` pointers. This is the only dependency we "carry" between different iterations inside the CPU as it's executing our code, it's unavoidable (well, I couldn't, maybe you can), but worth-while mentioning nonetheless. If you need a reminder about how dependencies can change the dynamics of efficient execution you can read up on when I tried my best to go at it battling with [`PopCount` to run screaming fast](2018-08-20-netcoreapp3.0-intrinsics-in-real-life-pt3.md).

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
Anyone who's followed the C# code can use the intrinsics table from the previous post and really read the assembly code without further help. If that's not a sign that the JIT is literally taking our intrinsics straight to the CPU as-is, I don't know what is!

## Permutation lookup table

The permutation lookup table is the elephant in the room at this stage, so let's see what's in it:

* The table needs to have 2<sup>8</sup> elements for all possible mask values.
* Each element ultimately needs to be a `Vector256<int>` because that's what Intel expects from us, so 8 x 4 bytes = 32 bytes per element.
  * That's a whopping 8kb of lookup data in total (!).
* The values inside are pre-generated so that they would shuffle the data *inside* the SIMD register in such a way that all values that got a corresponding `1` bit in the mask go to one side (right side), and the elements with a `0` go to the other side (left side). There's no particular required order amongst the grouped elements since we're partitioning around a pivot value, nothing more, nothing less.

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

* For `mask` values 0, 255 the entries are simple to grok: All bits were either `1` or `0` in the `mask` so there's nothing we need to do with the data, we just leave it as is, the permutation vector: `[0, 1, 2, 3, 4, 5, 6, 7]` achieves just that.
* When `mask` is `0b00000111` (7), the 3 lowest bits of the `mask` are `1`, they represent elements that need to go to the right side of the vector (e.g. elements that were `> pivot`), while all other values need to go to the left (`<= pivot`). The permutation vector: `[3, 4, 5, 6, 7, 0, 1, 2]` does just that.
* The checkered bit patten for the `mask` value `0b10101010` (170) calls to move all the even elements to one side and the odd elements to the other... You can see that `[0, 2, 4, 6, 1, 3, 5, 7]` does the work here.

If you look at the actual code, you'll see that the values inside the permutation table in the code are actually coded as a `ReadOnlySpan<byte>`. This is a CoreCLR / C# 7.3 specific optimization that allows us to treat the address of this table as a constant at JIT time. Kevin Jones ([@vcsjones](https://twitter.com/vcsjones)) did a wonderful job of digging into it, go [read his excellent post](https://vcsjones.dev/2019/02/01/csharp-readonly-span-bytes-static/) about this.
{: .notice--info}

It's **important** to note that this **must** to be a `ReadOnlySpan<byte>` for the optimization to trigger (that was two nights of my life chasing what I was sure had to be a GC / JIT bug). Now, normally, it would really be a **bad** idea to store a `ReadOnlySpan<int>` as a `ReadOnlySpan<byte>` since that forces us to "choose" between little/big endian encoding *at compile time*, and in C# we have to *assume* our code might run both on little/big endian machines where our actual CPU might not use the same encoding as we compiled with. Not fun! **In this case**, luckily,  this is a *non-issue*, as the entire premise is Intel specific, and we can simply assume little endianess here till the end of all times.
{: .notice--warning}

We've covered the basic layout of the permutation table. We'll go back to it once we start optimization efforts, but let us finish the vectorized partition first.

## Outer-Loop & Function

We now have a short partitioning block at hand, but there's a major complication: In-place sorting.

In-place sorting brings a new challenge to the table: While our block partitions 8-elements cleanly and quickly, the partitioned data *inside the SIMD register* contains **both** values smaller *and* larger than the pivot. Each "portion" of that register needs to end up on different ends of the array... that's kind of the whole idea with partitioning.

As shown previously, when we toured the vectorized partitioning block, it ends with writing the partitioned data into **both** sides of the array (remember `writeLeft` & `writeRight`). I fondly named this approach in my code as a "double-pumped" partitioning (and named the whole thing `AVX2DoublePumped<T>.QuickSort`), as it pumps values into **both** ends of the array. This is a tricky approach since we read some data, but *can't write* it back to the **same** address... that admittedly does not sound very in-place-y... But we'll soon dissect it to bits and see how/why this works.

This is the initial / approace version I got working that I was actually happy with, I've left it pretty much intact in the code repo under the name [`AVX2DoublePumpedNaive`](https://github.com/damageboy/QuicksortAvaganza/blob/master/QuickSortAvaganza/AVX2DoublePumpedNaive.cs), in case you want to dig through the real code... This approach, like all good things in life comes in 3-parts: Setup, a double-pumped loop and handling the remainder (that's too small for SIMD registers), so let's start:

### Setup: Make some room!

What I eventually opted for was to read from *one* area and write to *another* area in the same array. But we need to make some spare room inside the array for this. How? 

We cheat! (¯\\_(ツ)_/¯), but not really: we allocate some temporary space, using `stackalloc` in C#, here's why this isn't really cheating:

* Stack allocation doesn't put pressure on the GC, and it's allocation is super fast/slim.
* We allocate once at the top of our `QuickSort` and reuse that temporary space while recursing.
* How much is "just a bit"? For our 8-element partition block we need room for 1 x 8-elements vector on **each** side of the array, so we allocate 2 x 8 integers, or 64 bytes in total.

Now that we have some temporary memory, we simply read ahead 1 x 8-element vector from each side, and use our good-ole' partitioning block to partition straight **into** the temporary memory.

Having done that, we don't care about the area we just read from the array anymore, as it's partitioned away in the temporary memory, and we're free to write up to one SIMD vector to both ends of the array.  
We've now made enough room inside our array available for writing in-place while partitioning: we finish the setup by initializing read and write pointers for every side (`readLeft`, `readRight`, `writeLeft`, `writeRight`). An alternative way to think about them is that each side gets its own head (read) and tail (write) pointers. We will end up reading from **one** of the heads and write to **both** tails.

Here's the signature + setup code:

```csharp
static unsafe int* VectorizedPartitionInPlace(int* left, int* right, int *tmp)
{
    var pivot = *right;
    var readLeft = left;
    var readRight = right - 1;
    var writeLeft = readLeft;
    var writeRight = readRight - 8;

    var tmpLeft = tmp;
    var tmpRight = tmpLeft + TMP_SIZE_IN_ELEMENTS - 8;

    // Broadcast the selected pivot
    var P = Vector256.Create(pivot);
    var pBase = IntPermTablePtr;

    // Read ahead from left+right
    var LT0 = LoadDquVector256(readLeft + 0*8);
    var RT0 = LoadDquVector256(readRight - 1*8);

    var rtMask = (uint) MoveMask(CompareGreaterThan(LT0, P).AsSingle());
    var ltMask = (uint) MoveMask(CompareGreaterThan(RT0, P).AsSingle());

    var ltPopCount = PopCount(rtMask);
    var rtPopCount = PopCount(ltMask);

    LT0 = PermuteVar8x32(LT0, GetIntPermutation(pBase, rtMask));
    RT0 = PermuteVar8x32(RT0, GetIntPermutation(pBase, ltMask));

    Avx.Store(tmpRight, LT0);
    tmpRight -= ltPopCount;
    ltPopCount = 8 - ltPopCount;
    Avx.Store(tmpRight, RT0);
    tmpRight -= rtPopCount;
    rtPopCount = 8 - rtPopCount;
    tmpRight += 8;

    Avx.Store(tmpLeft, LT0);
    tmpLeft += ltPopCount;
    Avx.Store(tmpLeft, RT0);
    tmpLeft += rtPopCount;

    // Adjust for the reading that was made above
    readLeft  += 1*8;
    readRight -= 2*8;    
    // ... Rest of the code follows in the next paragraph
```

I've cut out the comments, but it's all available with much more detail and context in the repo, there's not a lot going on here for now: The function accepts parameters (`left`,`right`,`tmp`), we already expect the pivot to be selected and to have `right` point to it (We'll cover that in a bit), and all that you're seeing here are 2 partition blocks going on at the same time: partitioning a single vector from the left side, and a single vector from the right.

The setup fragment ends with `readLeft` being advanced by a `Vector256<int>` size , and `readRight` being decremented by the size of 2 `Vector256<int>`. This might seem peculiar at first, but don't forget that when we read/write using `Avx2.LoadDquVector256`/`Avx.Store` we always have to supply the start address to read from or write to! There is no ability to read/write to the "left" of the pointer, so this really isn't anything more than accounting for that.

### Double Pumped Loop

Here's a visual aid for how I ended up doing this; note the different color codes and legend I've provided here, and try to watch a few loops of this noticing the various color transitions, this will become useful for parsing the text below:

<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/double-pumped-loop-with-hint.svg"></object>
<object style="margin: auto" type="image/svg+xml" data="../talks/intrinsics-dotnetos-2019/double-pumped-loop-legend.svg"></object>
* Each rectangle is 8-elements wide.
* Initially we have an "area" inside the array we want to read from (orange) and another "area" we can write to (gray) on both ends (try to think where each head/tail is pointing to).
* In every round we first choose where we read from next: left/right side of the orange area?
  * How? Easy-peasy: Which ever side has a **smaller** gray area!
  * *Intuition*: The gray area represents the distance between the head (read) and tail (write) pointers we setup for each side, the smaller the distance / area is, the more likely that our next 8-element partition *might* end with us overwriting that side's head with the tail.
  * **We really don't want that to happen...**
  * So we read from the side *where this is more likely* to happen, thereby adding 8 more elements of breathing space to that side (you can see this clearly in the animation as each orange block turns gray *after* we read it, *but before* we write to both sides...)
* Once the data was read, we partition in-place with our trusty block, I've marked the internal "split" inside the register with green/red colors, for smaller-than-or-equal to the pivot value (green) and greater-than the pivot (red).
* Now we need to write that data to the next write position on each side, and we do so.
* We also need to advance each write pointer according to how much of that register was red/green, you can see this reflected on how the red portion of the copy on the left-hand side turns into gray, and the green portion on the right-hand side turns into gray correspondingly.  
  *Reminder*: We've already seen the code in detail when we discussed the partitioning block, I repeat it here since it is obviously critical to understand how the whole processing clicks together.

Here's the same loop in C#:

```csharp
    while (readRight >= readLeft) {
		int *nextPtr;
        if ((readLeft   - writeLeft) <=
            (writeRight - readRight)) {
            nextPtr = readLeft;
            readLeft += 8;
        } else {
            nextPtr = readRight;
            readRight -= 8;
        }

        var current = LoadDquVector256(nextPtr);
        var mask = (uint) MoveMask(CompareGreaterThan(current, P).AsSingle());
        current = PermuteVar8x32(current, GetIntPermutation(pBase, mask));

        Store(writeLeft, current);
        Store(writeRight, current);

        var popCount = PopCount(mask);
        writeRight -= popCount;
        writeLeft += 8U - popCount;
    }
```

Most of the loop body is the partition block we've already been through before. The only novelty here is the rather complex condition with which we select what side to read from next:

```csharp
        if ((readLeft   - writeLeft) <=
            (writeRight - readRight)) {
            nextPtr = readLeft;
            readLeft += 8;
        } else {
            nextPtr = readRight;
            readRight -= 8;
        }
```

This condition does in code what we described with animation / words before: it calculates the distance between each head and tail on each side and compares them to determine which side has less space left, or which side closer to being overwritten, since the next read will happen from that sie, we've just added 8 more integers worth of writing space to that same side, there-by eliminating the risk of overwriting...  
While it might be easy to read in terms of correctness or motivation, this is a very sad line of code, as it will haunt us in the next post!

### Handling the remainder and finishing up

Finally, we come out of the loop once we have less than 8-elements to partition. We obviously can't use vectorized code here, so we just do plain old scalar partitioning:

* To keep things simple, We partition the last elements right into the temporary area we used at the top of the function to make room for the main-loop
  * That means allocating 8 more elements in the temporary area, (It also means I lied before about using only 64 bytes, it's 96 bytes).

Once we're done with this little trailing scalar partitioning, we simply need to copy back our already partitioned data from the temporary area back into the array to the area left between `writeLeft` and `writeRight`, it's a quick 96 byte copy operation and we are finally done with partitioning, we just need to move the pivot back to the newly calculated pivot position and report this position back as the return value for this to be officially be christened as AVX2 partitioning function!

Here's the final piece of this function:

```csharp
    var boundary = writeLeft;

    // We're scalar from now, so
    // correct the right read pointer back
    readRight += 8;

    while (readLeft < readRight) {
        var v = *readLeft++;

        if (v <= pivot) {
            *tmpLeft++ = v;
        } else {
            *--tmpRight = v;
        }
    }

    var leftTmpSize = (int) (tmpLeft - tmp);
    new ReadOnlySpan<int>(tmp, leftTmpSize).
        CopyTo(new Span<int>(boundary, leftTmpSize));
    boundary += leftTmpSize;
    var rightTmpSize = (int) (tmp + TMP_SIZE_IN_ELEMENTS - tmpRight);
    new ReadOnlySpan<int>(tmpRight, rightTmpSize).
        CopyTo(new Span<int>(boundary, rightTmpSize));

    // Shove to pivot back to the boundary
    Swap(boundary++, right);
    return boundary;
}
```

## From the top

Now that we have a vectorized partitioning function, we're just missing the top-level code that does temporary stack allocation, pivot selection and recursion. We've covered the scalar variant of this in the first post, but let's look at our real/final function. This is pretty much copy-pasted with minor adaptations from the [CoreCLR code](https://github.com/dotnet/coreclr/blob/master/src/System.Private.CoreLib/shared/System/Collections/Generic/ArraySortHelper.cs#L182) that does the same for obvious reasons:

```csharp
public static partial class AVX2DoublePumpedNaive<T> 
  where T : unmanaged, IComparable<T>
{
    const int SLACK_PER_SIDE_IN_VECTORS  = 1;
    const int SLACK_PER_SIDE_IN_ELEMENTS = SLACK_PER_SIDE_IN_VECTORS * 8;
    const int TMP_SIZE_IN_ELEMENTS       = 2 * SLACK_PER_SIDE_IN_ELEMENTS + 8;

    public static unsafe void QuickSort(T[] array)
    {
        fixed (T* p = &array[0]) {
            if (typeof(T) == typeof(int)) {
                // We use this much space for making some room inside
                // the array + partitioning the remainder
                var tmp = stackalloc int[TMP_SIZE_IN_ELEMENTS];

                var pInt = (int*) p;
                QuickSortInt(pInt, pInt, pInt + array.Length - 1, tmp);
            }
        }
    }
    //...
}
```

This is the main entry point, where we special case using generic type elision and simply call our signed integer version `QuickSortInt` after allocating the temporary memory. This is as good time as any to mention that for the time being, I only implemented vectorized quick-sorting when `T` is `int`. To fully replace `Array.Sort` many more tweaked versions of this code will have to be written to eventually support unsigned integers, both larger and smaller than 32 bits and floating point types.

Back to the existing code, though: once we know for sure that `T` is an `int`, we go into `QuickSortInt`:

```csharp
static unsafe void QuickSortInt(int* start, int* left, int* right, int *tmp)
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
            SwapIfGreater(mid, right);
            return;
    }

    // Go to insertion sort below this threshold
    if (length <= 16) {
        InsertionSort(left, right);
        return;
    }

    // Compute median-of-three, of:
    // the first, mid and one before last elements
    mid = left + ((right - left) / 2);
    SwapIfGreater(left, mid);
    SwapIfGreater(left, right - 1);
    SwapIfGreater(mid, right - 1);
	// Pivot is mid, place it in the right hand side
    Swap(mid, right);

    var sep = VectorizedPartitionInPlace(left, right, tmp);

    QuickSortInt(start,  left, sep - 1, tmp);
    QuickSortInt(start, sep, right, tmp);
}
```

This is really the part I blatantly copied for [`ArraySortHelper<T>`](https://github.com/dotnet/coreclr/blob/master/src/System.Private.CoreLib/shared/System/Collections/Generic/ArraySortHelper.cs#L182). What it does is:

* Special cases for lengths of 0,1,2,3
* When length `<= 16` we just go straight to `InsertionSort` and skip all the recursive jazz (go pack to post 1 if you want to know why they did that).
  * 
* When we have `>= 17` elements, we go to vectorized partitioning: 
  * we do median of 3 pivot selection
  * swap that pivot so that it resides on the right most index of the partition.
* Then we either call `VectorizedPartitionInPlace`, which we've seen before.
* Recurse to the left.
* Recurse to the right.

## Initial Performance

Are we fast yet?

Yes! This is by no means the end, on the contrary, this is only. We now have something working, and it is even not too shabby, if I may say so:

```bash
BenchmarkDotNet=v0.11.5, OS=clear-linux-os 30850
Intel Core i7-7700HQ CPU 2.80GHz (Kaby Lake), 1 CPU, 4 logical and 4 physical cores
.NET Core SDK=3.0.100-rc1-014015
  [Host]     : .NET Core 3.0.0-rc1-19425-03 (CoreCLR 4.700.19.42204, CoreFX 4.700.19.42010), 64bit RyuJIT
  Job-PDGVYD : .NET Core 3.0.0-rc1-19425-03 (CoreCLR 4.700.19.42204, CoreFX 4.700.19.42010), 64bit RyuJIT

InvocationCount=10  IterationCount=3  LaunchCount=1
UnrollFactor=1  WarmupCount=3
```

| Method           | N        |    Mean (usec) | Time / N (nsec) | Ratio |
| ---------------- | -------- | -------------: | --------------: | ----: |
| ArraySort        | 100      |          1.926 |      19.2578 |  1.00 |
| AVX2DoublePumpedNaive | 100      |          1.675 |      16.7518 |  1.05 |
| ArraySort        | 1000     |      29.489 |      29.4890 |  1.00 |
| AVX2DoublePumpedNaive | 1000     |      25.738 |      25.7378 |  0.87 |
| ArraySort        | 10000    |     539.452 |      53.9452 |  1.00 |
| AVX2DoublePumpedNaive | 10000    |     326.388 |      32.6388 |  0.60 |
| ArraySort        | 100000   |   6,008.936 |      60.0894 |  1.00 |
| AVX2DoublePumpedNaive | 100000   |   3,455.113 |      34.5511 |  0.58 |
| ArraySort        | 1000000  |  69,429.272 |      69.4293 |  1.00 |
| AVX2DoublePumpedNaive | 1000000  |  27,297.568 |      27.2976 |  0.39 |
| ArraySort        | 10000000 | 804,821.776 |      80.4822 |  1.00 |
| AVX2DoublePumpedNaive | 10000000 | 295,116.936 |      29.5117 |  0.37 |

We're off to a very good start: We can see that as soon as we hit 1000 element arrays (even earlier, in earnest) we already outperform `Array.Sort()` (87% runtime), and by the time we get to 1M / 10M element arrays, we are actually speedups north of 2.5x (39%, 37% runtime) over the scalar C++ code!  
I've added a custom column to the BDN summary: `Time /N`. This tries to show the time spent per element in the array. You can clearly see that while `Array.Sort()` is behaving like we would expect from a `QuickSort` function, given that it has a Big-O notation of $$\mathcal{O}(n\log{}n)$$, our `AVX2DoublePumpedNaive` is slightly peculiar: The time spent sorting each single element initially goes up as the array increases exponentially in size, then goes down a bit and back up. Why is that?  
Remember that we have scalar insertion sort and vectorized code executing together. Where are we actually spending most of the time though?  
It's time we profile the code to see what's really up: We can fire up the venerable Linux `perf` tool, through a simple test binary and project I've coded up which allows me to execute some dummy sorting by selecting the sort method I want to invoke and specify some parameters for it through the command line, for example:

```bash
$ cd ~/projects/public/QuickSortAvaganza/Example
$ dotnet publish -c release -o linux-x64 -r linux-x64
# Run AVX2DoublePumped with 1,000,000 elements x 100 times
$ ./linux-x64/Example AVX2DoublePumpedNaive 1000000 100
```

Will call the `AVX2DoublePumpedNaive` implementation we've been discussing from the beginning of this post with 1M elements, and re-sort the same random data 100 times to generate some heat in case global warming isn't not cutting it for you.  
I know this seems like calling `dotnet publish ...` is superfluous, but for some reason it's important for our next step of running this under `perf` (`dotnet run` ddn't work for me and I didn't care investigating) so go with me here:

```bash
# Make the JIT speak to 'perf'
$ export COMPlus_PerfMapEnabled=1 
# Record some performance information, namely the 'instructions' HW counter,
# For 1M elements, 100 invocations
$ perf record -F max -e instructions ./Example AVX2DoublePumped 1000000 100
info: Using a maximum frequency rate of 100,000 Hz
[ perf record: Woken up 45 times to write data ]
[ perf record: Captured and wrote 11.098 MB perf.data (290031 samples) ]
$ perf report --stdio -F overhead,sym | head -15
...
# Overhead  Symbol
    65.66%  [.] ... ::VectorizedPartitionInPlace(int32*,int32*,int32*)[Optimized]
    22.43%  [.] ... ::InsertionSort(!!0*,!!0*)[Optimized]
     5.43%  [.] ... ::QuickSortInt(int32*,int32*,int32*,int32*)[OptimizedTier1]
     4.00%  [.] ... ::Memmove(uint8&,uint8&,uint64)[OptimizedTier1]

# Same shtick, 10K elements, 10K invocations
$ perf record -F max -e instructions ./Example AVX2DoublePumpedNaive 10000 10000
info: Using a maximum frequency rate of 100,000 Hz
[ perf record: Woken up 38 times to write data ]
[ perf record: Captured and wrote 9.549 MB perf.data (250052 samples) ]
$ perf report --stdio -F overhead,sym | head -15
...
# Overhead  Symbol
    54.59%  [.] ... ::VectorizedPartitionInPlace(int32*,int32*,int32*)[Optimized]
    29.87%  [.] ... ::InsertionSort(!!0*,!!0*)[Optimized]
     7.02%  [.] ... ::QuickSortInt(int32*,int32*,int32*,int32*)[OptimizedTier1]
     5.23%  [.] ... ::Memmove(uint8&,uint8&,uint64)[OptimizedTier1]
```

This is a trimmed summary of recording performance metrics, specifically, number of instructions executed for running a 1M element sort 100 times, followed by running a 10K element sort, 10K times. I hope you're both shocked as I was when I saw this for the first time, but also starting to understand the previous oddities we saw with the `Time /N` column!  
We're spending upwards of 20% of the our time doing scalar insertion sorting! This was supposed to be vectorized sorting and yet, somehow, "only" 65% of the time is spent in the vectorized function (which also has some scalar parts). Not only that, but as the size of the array decreases, the percentage of time spent in scalar code *increases*, which should not really surprise us anymore.  
Before anything else, let me assure you that changing the threshold for insertion sort (e.g. 16 elements) doesn't yield improvements to the total runtime of the sort operation. It does change the mix, but not so much the total time spend sorting.

This is but the beginning of our profiling journey, but we are already learning a complicated truth: Right now, as fast as this is already going, the scalar code we've used for insertion sort will always put an upper limit on how fast we can possibly go, unless we get rid of it and replace it with something better. But first thing, we need to start from the top: 65% of instructions here are still spent doing vectorized sorting. So let's focus on making that go faster, since it's still the biggest target on our scope!

## Finishing off with a sour taste

There one last thing I want to end this post with, let's re-run `perf` and see how is our code behaving in terms of branch mis-prediction. What I'll do is use very useful linux utility called [`cset`](https://github.com/lpechacek/cpuset) which can be [used to](https://stackoverflow.com/a/13076880/9172) evacuate all user threads and (almost all) kernel threads from a given CPU core, using cpusets, then execute our `Example` binary on that CPU while collecting various top-level statistics:

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

Once we have that shield setup, we will run `perf` while scheduling our stuff into CPU #3 so that we know that whatever happens on that CPU is all due to our code...

```bash
$ perf stat -a --topdown sudo cset shield -e ./Example AVX2DoublePumped 1000000 100
cset: --> last message, executed args into cpuset "/user", new pid is: 16107

 Performance counter stats for 'system wide':
        retiring      bad speculation       frontend bound        backend bound
S0-C0 1    27.2%                10.2%                27.0%                35.5%
S0-C1 1    25.2%                 6.5%                30.0%                38.4%
S0-C2 1    24.1%                 7.0%                29.7%                39.2%
S0-C3 1    37.6%                32.3%                16.9%                13.2%

       3.221968791 seconds time elapsed

```

Well, here are some bad news: core #3 is really not having a good time running our code with 32.3% of the branches taken being mis-speculated. This might not be immediately apparent, but this is really bad. The penalty for each bad speculation is an entire flush of the pipeline, which costs around 14-15 cycles on modern Intel CPUs.  
But wait, maybe it's not our vectorized code? Maybe we can chalk it up all on that mean scalar `InsertionSort` I was so happy to blame two paragraphs ago? We are, after all, using it for sorting small partitions, which we already saw to be taking more than 20% of the total run time? Let's see again with `perf`, this time focusing on the `branch-misses` HW counter:

```bash
$ export COMPlus_PerfMapEnabled=1 # Make perf speak to the JIT
# Record some performance information:
$ perf record -F max -e branch-misses ./Example AVX2DoublePumped 1000000 100
info: Using a maximum frequency rate of 100,000 Hz
[ perf record: Woken up 45 times to write data ]
[ perf record: Captured and wrote 11.098 MB perf.data (290031 samples) ]
$ perf report --stdio -F overhead,sym | head -17
...
    40.97%  [.] ...::InsertionSort(!!0*,!!0*)[Optimized]
    32.30%  [.] ...::VectorizedPartitionInPlace(int32*,int32*,int32*)[Optimized]
     9.64%  [.] ...::Memmove(uint8&,uint8&,uint64)[OptimizedTier1]
     9.64%  [.] ...::QuickSortInt(int32*,int32*,int32*,int32*)[OptimizedTier1]
     5.62%  [.] ...::VectorizedPartitionOnStack(int32*,int32*,int32*)[Optimized]
...
```

No such luck. While `InsertionSort` is definitely starring here with 41% *of the* bad speculation events, we still have **32%** of the bad speculation coming from our own so-called branch-less vectorized code. This is a lot given that our entire 8-element partition block has a throughput of around  6-7 cycles. This means we are hitting a 15 cycle wall quite often to feel good about ourelves.

I'll finish here with that. We have a lot of work cut out for us. This is no-where near over.  
In the [next post]({% post_url 2019-08-21-trumping-arraysort-with-avx2-pt4 %}), I'll try to give the current vectorized code a good shakeup. After all, it's still our biggest target in terms of number of instructions executed, and 2<sup>nd</sup> when it comes to branch mis-prediction. Once we finish squeezing that piece of code on the 4<sup>th</sup> post, We will turn our focus on the 5<sup>th</sup> post to the `InsertionSort` function, and we'll see if there's anything to be done about it.  
In the meantime, you can try and go back to the vectorized partitioning function and try to figure out what is causing all those awful branch mis-predictions of you're up for a small challenge...