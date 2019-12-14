---
title: "Unsafe Bounds Checking"
header:
  image: /assets/images/coreclr-clion-header.jpg
date: 2019-11-08 08:26:28 +0300
classes: wide
#categories: unsafe debugging
---

# Unsafe Bounds Checking

I thought I'd write a really short post on a nifty technique/trick I came up while trying to debug my own horrible unsafe code for vectorized sorting. I don't think I've seen it used/shown before, and it really saved me tons of time.
It all boils down to a combination of:

* `using static`
* `#if DEBUG`
* Local functions in C#

Imagine this is our starting point:

```csharp
unsafe void GenerateRollingSum(int *p, int lengthInVectors)
{
    // This get's folded as a constant by the
    // JIT and I hate typing this all over the place
    var N = Vector256<int>.Count;

    var acc = Avx.LoadDquVector256(p);
    var pEnd = p + lengthInVectors * N;
    var pRead = p + 1;
    var pWrite = p;
    while (p < pEnd) {
      var data = Avx.LoadDquVector256(p);
      acc = Avx.Add(data, acc);
      Avx.Store(pWrite, acc);
    }
}
```

I'm providing here a very **wrong** implementation, obviously, for the purpose of this post. Keen eyes will immediately notice that this method is going to make us very unhappy as it is writing partially into the same memory it is about to read in the next iteration. It's definitely not going to work. But at the same time, it's important to note that it isn't going to crash or generate any exception, except for not doing it's job.

Unfortunately, for me, I've managed to write many variations of this bug, so I had to come up with something that would negate my in-built idiocy, here's what I normally write with code like this these days:

```csharp
// We import all the static methods in Avx
using static System.Runtime.Intrinsics.X86.Avx;

unsafe void GenerateRollingSum(int *p, int lengthInVectors)
{
    // This get's folded as a constant by the
    // JIT and I hate typing this all over the place
    var N = Vector256<int>.Count;

    var acc = LoadDquVector256(p);
    var pEnd = p + lengthInVectors * N;
    var pRead = p + 1;
    var pWrite = p;
    while (p < pEnd) {
      var data = LoadDquVector256(p);
      acc = Avx.Add(data, acc);
      Store(pWrite, acc);
    }

#if DEBUG
    // "Hijack" LoadDquVector256 under DEBUG configuration
    // and assert for various constraint violations
    Vector256<int> LoadDquVector256(int *ptr) {
      Debug.Assert((ptr + N - 1) < p + lengthInVectors * N,
                   "Reading past end of array");
      // Finally call the real LoadDquVector256()
      return Avx.LoadDquVector256(ptr);
    }

    // "Hijack" LoadDquVector256 under DEBUG configuration
    // and assert for various constraint violations
    void Store(int *ptr, Vector256<int> data) {
      Debug.Assert((ptr + N - 1) < p + lengthInVectors * N,
                   "Writing past end of array");
      Debug.Assert((ptr + N - 1) < pRead,
                   "Writing will overwrite unread data");
      // Finally call the real Store()
      Avx.Store(ptr, data);
    }
#endif
}
```

As you can see, this is a nifty way to abuse `using static` statements with local functions. We override the `LoadDquVector256()` / `Store` intrinsics only in `DEBUG` mode, so there's no performance hit that they incur in `RELEASE`, and we also make use of the fact that they are defined as local functions to perform some in-depth `Debug.Assert()`ing  that is based on the internal state of the function. Without defining these functions as local we would not be able to do so...

This isn't necessarily useful for vectorized code exclusively, but any code that is potentially tricky. I hope you find this useful! I don't think I've seen this in the wild before.
