# linear_278

## 原始程序（未修改）

```c
/*@ requires k > i - j && i < j; */
void foo278(int i, int j, int k) {



    while (i < j) {
       k = k + 1;
       i = i + 1;
      }

    /*@ assert k > 0; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：7cb293df04abcb6515975f6f32994df2c2a8029bdc6447f6efefde07799d0fa8。

## 冻结不变量（原样列出）

```text
i <= j
k == \at(k, Pre) + (i - \at(i, Pre))
i - k >= \at(i, Pre) - \at(k, Pre)
k >= \at(k, Pre)
0 <= i - \at(i, Pre)
k <= \at(k, Pre) + ( \at(j, Pre) - \at(i, Pre) )
i <= j && k == \at(k, Pre) + (i - \at(i, Pre))
\at(i, Pre) <= i
i <= j && k >= \at(k, Pre)
0 <= k - \at(k, Pre)
i - j <= 0
j - i >= 0
k - i == \at(k, Pre) - \at(i, Pre)
k == k
k - (k) == 0
i == i
j >= \at(j, Pre)
(j - i) + i == j
k - i == k - i
k <= k + (j - i)
(i - \at(i, Pre)) == 0 ==> (k - \at(k, Pre)) >= 0
i - (i) == 0
i + k <= j + k
i - j == - (j - i)
j - i >= k - k
k - \at(k, Pre) == i - \at(i, Pre)
0 <= j - \at(j, Pre)
i - k == i - k
k >= \at(k, Pre) + (i - \at(i, Pre))
i == \at(i, Pre) + (k - \at(k, Pre))
k - i >= \at(k, Pre) - \at(i, Pre)
i <= j ==> k - i <= \at(k, Pre) - \at(i, Pre) + (j - \at(j, Pre))
\at(j, Pre) >= j
(k - i) == (\at(k, Pre) - \at(i, Pre)) + (i - \at(i, Pre)) - (i - \at(i, Pre))
k - i <= \at(k, Pre) - \at(i, Pre) + (j - \at(j, Pre))
i <= j ==> k >= \at(k, Pre) + (i - \at(i, Pre))
i <= j ==> k == \at(k, Pre) + (i - \at(i, Pre))
i - \at(i, Pre) == i - \at(i, Pre)
i <= \at(j, Pre)
i >= 0 ==> k >= \at(k, Pre)
i <= j ==> k <= \at(k, Pre) + (j - \at(i, Pre))
\at(i, Pre) <= j
i - k == \at(i, Pre) - \at(k, Pre)
i <= j ==> k - i == \at(k, Pre) - \at(i, Pre)
i - \at(i, Pre) == 0 ==> k - \at(k, Pre) == 0
i - j < 1
i - j < 0 ==> i <= j
i + k == i + k
i - j == i - j
```

## 本次人工设计的复用目标

- H: k−i=k₀−i₀
- H: i₀≤i≤j
- E: i=j
- E: k=k₀+j−i₀
- E: k>k₀

j 不变，退出时 i≥j，故 i=j；由精确差值恢复 k 的输出值，且入口 i₀<j。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
