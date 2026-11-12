# linear_16

## 原始程序（未修改）

```c
int unknown();
void foo16(int n) {
    int x = 0;
    int m = 0;

    
    while (x < n) {
        
        if (unknown()) {
            m = x;
        }
        
        x = x + 1;
    }

    /*@ assert (n > 0) ==> (m >= 0); */

}
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：11c03d8ec53d622f8c9e346914fc402ee4d47ab8294a783c911a5dbaa50eb6b4。

## 冻结不变量（原样列出）

```text
0 <= x
m <= x
(m >= 0) ==> (m <= x)
x - m >= 0
x == m || x == m + (n - m) || x <= n
(x <= n) ==> (m <= x)
0 <= m
x - m == 0 ==> m == x
x == m + (x - m)
(x < n) ==> (x + 1 <= n)
x == 0 ==> m == 0
(m == x - 1) ==> (x > 0)
x - m == (x - m)
x == m || x > m
(x == n) ==> (m <= n)
(x < n) ==> (m <= x)
(x >= 0) ==> (m >= 0)
(x == n) ==> (m == m)
x <= n ==> (m == 0 || m <= x)
(m == 0) ==> x >= 0
(x >= 0) ==> (m <= x || m == 0)
(x == n) ==> (m <= x)
x >= 0 && m >= 0
(m != 0) ==> (m < x)
0 <= m && m <= x
m <= x ==> m == x || m == m
(m < x) ==> (x <= n)
(m != 0) ==> (m <= x - 1)
(x <= n) ==> (x - m >= 0)
(m < x) ==> (m == 0 || m < x)
(m == x) ==> (m >= 0)
```

## 本次人工设计的复用目标

- H: 0≤m≤x
- H: m≠0⇒m<x
- E: x=max(n,0)
- E: n>0⇒0≤m<n

若 x=m，则严格关系迫使二者为零；结合其余析取与退出 x≥n 得到精确终点及选中下标范围。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
