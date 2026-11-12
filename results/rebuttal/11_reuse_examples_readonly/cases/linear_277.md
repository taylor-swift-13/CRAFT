# linear_277

## 原始程序（未修改）

```c
/*@ requires n >= 0 && n <= 20000001; */
void foo277(int n) {

    int i;
    int k;

    i = 0;
    k = n;


    while (i < n) {
       k--;
       i += 2;
      }

    /*@ assert 2 * k >= n - 1; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：eff3369a057430da27311e45948080088b7814403cf942e4a279b71160d5c0cd。

## 冻结不变量（原样列出）

```text
i >= 0
k >= 0
i % 2 == 0
i >= 0 && k >= 0
i == 2 * (i / 2)
(i < n) ==> (k >= 0)
(i >= 0) ==> (k <= n)
i % 2 == 0 ==> i - 0 >= 0
i <= 2 * ((n + 1) / 2)
k <= n
k == n - i/2
(k >= 0) ==> ((n - i/2) >= 0)
(i <= n) ==> (k == n - i/2)
(i % 2 == 0) ==> (k == n - i/2)
i == 2 * (n - k)
0 <= n - k
k % 1 == 0
```

## 本次人工设计的复用目标

- H: 2k+i=2n
- H: i mod 2=0
- E: i=2⌈n/2⌉
- E: k=⌊n/2⌋

入口 n≥0；退出 i≥n，结合偶数性和上界确定向上取偶数的终点。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
