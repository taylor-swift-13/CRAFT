# linear_263

## 原始程序（未修改）

```c
/*@ requires k >= 0 && k <= 20000001; */
void foo263(int k) {

    int n;
    int i;

    n = 0;
    i = 0;


    while (i < 2 * k) {
       if(i % 2 == 0){
       n = n + 1;
      }
       i = i + 1;
      }

    /*@ assert n == k; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：9e286896d68ccccf3eaf1a14ad0f159146155da46273cc46dbcffc3cd9353ffe。

## 冻结不变量（原样列出）

```text
0 <= i
i <= 2 * k
(i % 2 == 0) ==> (n == i / 2)
0 <= n
n <= k
i - n == n + (i - 2*n)
n <= i
0 <= k
n == (i / 2) + (i % 2)
(i % 2 == 1) ==> (n == (i - 1) / 2 + 1)
i >= 0 ==> n <= i
n <= k + 1
(i % 2) == 0 ==> (n * 2 == i)
(i % 2) != 0 ==> (2 * n - 1 == i)
!(i == 2 * k) ==> (i < 2 * k)
i <= 2 * k && i >= 0
(i % 2 == 0) == (n * 2 == i)
(i <= 2 * k) ==> (n <= k)
(i >= 0) ==> (n <= k)
i - 2 * n <= 1
(i % 2 != 0) ==> n == (i - 1) / 2 + 1
0 <= n <= k
(i % 2 == 0) || (i % 2 == 1)
i <= 2 * k && n == (i + 1) / 2
n + (2 * k - i) >= 0
(i % 2 == 0) ==> (n >= 0)
(i % 2 == 0) ==> (n <= i / 2 + k)
i == 0 ==> n == 0
```

## 本次人工设计的复用目标

- H: 2n−i∈{0,1}
- H: 0≤i≤2k
- E: i=2k
- E: i=2n

保存了中间状态的奇偶计数关系；退出时 i=2k，进而确定计数和迭代次数。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
