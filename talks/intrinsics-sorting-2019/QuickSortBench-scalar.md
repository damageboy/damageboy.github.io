``` ini

BenchmarkDotNet=v0.11.5, OS=ubuntu 19.04
Intel Core i7-7700HQ CPU 2.80GHz (Kaby Lake), 1 CPU, 4 logical and 4 physical cores
.NET Core SDK=3.0.100-preview5-011568
  [Host]     : .NET Core 3.0.0-preview5-27626-15 (CoreCLR 4.6.27622.75, CoreFX 4.700.19.22408), 64bit RyuJIT
  Job-NONELN : .NET Core 3.0.0-preview5-27626-15 (CoreCLR 4.6.27622.75, CoreFX 4.700.19.22408), 64bit RyuJIT

InvocationCount=10  IterationCount=3  LaunchCount=1  
UnrollFactor=1  WarmupCount=3  

```
|          Method |       N |          Mean |         Error |        StdDev |        Median | Ratio | RatioSD |
|---------------- |-------- |--------------:|--------------:|--------------:|--------------:|------:|--------:|
|       **ArraySort** |     **100** |      **1.383 us** |      **2.180 us** |     **0.1195 us** |      **1.387 us** |  **1.00** |    **0.00** |
| QuickSortScalar |     100 |      2.711 us |     21.465 us |     1.1766 us |      2.129 us |  2.02 |    1.05 |
| QuickSortUnsafe |     100 |      3.495 us |      1.379 us |     0.0756 us |      3.538 us |  2.54 |    0.17 |
|                 |         |               |               |               |               |       |         |
|       **ArraySort** |    **1000** |     **31.075 us** |     **60.866 us** |     **3.3363 us** |     **29.808 us** |  **1.00** |    **0.00** |
| QuickSortScalar |    1000 |     56.328 us |     30.305 us |     1.6611 us |     55.920 us |  1.82 |    0.14 |
| QuickSortUnsafe |    1000 |     52.166 us |    168.371 us |     9.2290 us |     47.895 us |  1.71 |    0.45 |
|                 |         |               |               |               |               |       |         |
|       **ArraySort** |   **10000** |    **533.889 us** |    **177.563 us** |     **9.7328 us** |    **532.174 us** |  **1.00** |    **0.00** |
| QuickSortScalar |   10000 |    730.180 us |    206.098 us |    11.2969 us |    728.355 us |  1.37 |    0.02 |
| QuickSortUnsafe |   10000 |    656.191 us |    321.329 us |    17.6131 us |    658.641 us |  1.23 |    0.04 |
|                 |         |               |               |               |               |       |         |
|       **ArraySort** |  **100000** |  **5,923.499 us** |    **910.595 us** |    **49.9128 us** |  **5,938.023 us** |  **1.00** |    **0.00** |
| QuickSortScalar |  100000 |  7,746.573 us |  1,258.196 us |    68.9660 us |  7,760.508 us |  1.31 |    0.02 |
| QuickSortUnsafe |  100000 |  7,101.699 us |    496.399 us |    27.2093 us |  7,109.938 us |  1.20 |    0.01 |
|                 |         |               |               |               |               |       |         |
|       **ArraySort** | **1000000** | **69,008.180 us** | **25,980.342 us** | **1,424.0702 us** | **68,257.100 us** |  **1.00** |    **0.00** |
| QuickSortScalar | 1000000 | 90,074.950 us | 30,340.150 us | 1,663.0460 us | 89,201.107 us |  1.31 |    0.00 |
| QuickSortUnsafe | 1000000 | 82,971.521 us | 25,498.826 us | 1,397.6767 us | 82,399.885 us |  1.20 |    0.04 |
