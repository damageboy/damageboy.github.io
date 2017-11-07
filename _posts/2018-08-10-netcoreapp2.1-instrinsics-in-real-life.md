---
title: ".NET Core 2.1 Intrinsics"
date: 2018-08-10 18:26:28 +0300
categories: coreclr instrinsics
---
I've recently overhauled and internal data structure we use in Work:registered: to start using the somewhat anticipated features (for speed junkies like me) that was released in preview form as part of Core CLR 2.1: [platform dependent intrinsics](https://github.com/dotnet/designs/blob/master/accepted/platform-intrinsics.md).

What follows is sort of a travel blog journey of what I did and how the new Core CLR functionality fairs to writing C++ code, when intrinsics are involved.

This will actually be split into 3 parts:

* The data-structure / operations that will be converted
* The C++ version, tests and benchmarks that will serve as a baseline for what comes later with CoreCLR
* The CoreCLR version

So without further ado, let's try to describe what we're trying to improve upon:

## DenseSortedList

Our subject for some perf work, is a somewhat curiously (for outside readers) data structure we call `DenseSortedList<TValue>`.

This is a custom data-structure that merely borrows its name from the .NET [`SortedList<TKey, TValue>` BCL Collection](https://docs.microsoft.com/en-us/dotnet/api/system.collections.generic.sortedlist-2?view=netframework-4.7.2).

### Detour: Why is `DenseSortedList<TV>` such a special snowflake

While `DenseSortedList` is essentially a dictionary-like object, as its borrowed name implies, there's a lot of domain specific stuff going on here, that justifies writing this from scratch:

* `TKey` is always an integer, and `TValue` can really be anything, though we actually only need it for  value-types (structs)
* We actually need various APIs to support both access / mutation by key and by index (more on indices in a moment)
* This really needs to be super-fast, as querying/updating this dictionary boils down to double digit % of our hot code path
* We absolutely need predictability here, both in the sense that:
  * deleting / inserting / updating new key-value-pairs has to be a very constant operation, e.g. O(*1*) 
  * We literally want t<sub>update</sub> == t<sub>insert</sub> == t<sub>delete</sub> 
* No allocations

#### Key ⇄ Index

In addition to just mapping key to value, throughout the entire API, we need to deal with the key's index.

This last quirk, requires a formal definition: As keys are sorted/removed (for the purpose of this definition, we assume ascending sorting only) we need, at any given moment, to be able to swiftly translate a given key, into its index,  where index would be defined as how many keys we have stored in our `DenseSortedList` that are smaller than that key.

Just to be super clear, here's an example:

When the dictionary contains the following keys, this is what their indices are:



| Key:   | 11342 | 11344 | 11345 | 11346 |
| ------ | ----- | ----- | ----- | ----- |
| Index: | 0     | 1     | 2     | 3     |

But two addition operations later:



| Key:   | 11340 | 11342 | 11343 | 11344 | 11345 | 11346 |
| ------ | ----- | ----- | ----- | ----- | ----- | ----- |
| Index: | 0     | 1     | 2     | 3     | 4     | 5     |

#### We know a lot about our keys

The reason we even bothered writing something like this (and this is coming from someone who'd rather cut off their own hands rather than re-implement classic data-structures for naught) is because unlike a generic `SortedList` like dictionary, we have specific insight about how this dictionary would be used: e.g. we know a-lot about the keys and their distribution:

* They are mostly dense: e.g. the incoming keys are successive integers > 80% of the time
* Even when their not strictly successive they'll be single digit distances away from the next/previous key
* Occasionally we do get very fat-tails in the distribution, so we need to account for those, and be able to deal with them, without optimizing for them

#### API

To make a long story short, we ended up, years ago, opting for a design where we provide the following key operations:

```csharp
public class DenseSortedList<TV> where TV : struct {
	// Update / Insert a new key, return value marks what actualy happenned
    // index marks the index of that key in the sorts list of keys
    public bool Upsert(int key, out TV value, out int index);
    // Returns the old index
    public int RemoveByKey(int key);
    // Returns the old key
    public int RemoveByIndex(int index);
    // Read 
    public ref TV GetByIndex(int index, out int key);
    public (ref TV, bool) TryGetByKey(int key, out int index);
}
```

#### Implementation

We're finally getting close to the original reason I wrote this thing, which was intrinsics, but to really get there we need to describe our actual implementation.

##### The Values

We ended up using a two-level array for the actual values where we have a master array, with each element pointing to yet another array, where the actual values are stored:

![Diagram](/assets/images/dense-sorted-list.svg)

So if you squint your eyes, you end up seeing what is essentially ends up being one pre-alocated array, with the ability to not pay in advance, too much memory, in case there's a big jump in the keys (more than 1,024)

Now, what becomes apparent very quickly is that without more work/information, this is really a design that is skewed towards accessing values by key where:

* Retrieving the value for a known key, is super fast:
  * Single Right Shift (`>>`) operation to find the right array (index)
  * Single bitwise and (`&`) operation to find the index within the array
* Deleting / Inserting new values for new keys does not require moving / copying a single bit in memory
* Keeping a two level mapping between keys/values allows us to deal with occasional large jumps in the key space

What should also become almost immediately apparent is what is not covered by the description so far:

* Since the array is really an array of value types, there is no way of telling which keys have been allocated(!)
* Even if we could in some magical way find values which have a valid mapping set up for them, doing any sort of index based retrieval / modification is extremely painful, as we need to literally traverse the entire value space to calculate the index

##### The Bitmap

The solution we opted for, which we feel fits well with our constraints / understanding of the data, was to use a presence bitmap that to mark the presence of a any specific key (e.g. if a given key is mapped to anything or not).

The bitmap completes the `DenseSortedList` implementation, now that we can actually figure out (and cheaply) if a given key is mapped (it's corresponding bit in the bitmap is `1`) or not-mapped (`0`)

What we are left with is basically implementing two functions that will:

1. Retrieve the index for a given key (`GetIndexForKey()`)
2. Retrieve the key for a given index (`GetKeyForIndex()`)

