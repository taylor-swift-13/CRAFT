# linear_267

## 原始程序（未修改）

```c
/*@ requires n >= 1 && k >= n; */
void foo267(int k, int n) {

    int j;

    j = 0;


    while (j <= n - 1) {
       j = j + 1;
       k = k - 1;
      }

    /*@ assert k >= 0; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：a3c83c656c5025187ce2193ed184fcca52500b71c2f14425cf76eacbb5ca4e4e。

## 冻结不变量（原样列出）

```text
j >= 0
j <= n
k + j == k + j
k == \at(k, Pre) - j
j == (k - \at(k, Pre)) * 0 + j
n >= 1
k >= 0
k + j <= \at(k, Pre) + n
j >= 0 && j <= n
k == n + (k - n)
k >= k - j
k >= n - j
k == k
k >= \at(k,Pre) - n
k - j == k - j
j == 0 ==> k == k
j <= n - 1 ==> k >= 1
j - 1 >= -1
k >= 0 && j >= 0
k + j <= k + n
k + j == n + (k - (n - j))
(j <= n) ==> (k >= n - j)
(j <= n) ==> (k + j <= k + n)
k == k - j + j
j <= n - 1 + 1
k <= k + (n - j)
k >= k - (n - j)
j >= 0 && k >= 0
k + j == \at(k,Pre)
(k >= 0) ==> (j >= 0)
k - (n - j) == k - n + j
```

## 本次人工设计的复用目标

- H: k+j=k₀
- H: 0≤j≤n
- E: j=n
- E: k=k₀−n

入口 n≥1、k₀≥n；退出 j>n−1 与上界确定 j=n，再恢复剩余额度。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
