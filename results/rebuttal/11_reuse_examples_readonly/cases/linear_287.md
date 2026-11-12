# linear_287

## 原始程序（未修改）

```c
/*@ requires x < 100 && y < 100; */
void foo287(int x, int y) {



    while (x < 100 && y < 100) {
       x = x + 1;
       y = y + 1;
      }

    /*@ assert x == 100 || y == 100; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：af19b1f8368746950d852095408e3249707748e3c268ad9f115790365b6c75af。

## 冻结不变量（原样列出）

```text
x <= 100
y <= 100
x <= y ==> x <= 100
y <= x ==> y <= 100
x - y == 0 ==> x <= 100
y - x == 0 ==> y <= 100
x <= 100 && y <= 100
x == y + (\at(x, Pre) - \at(y, Pre))
(\at(x, Pre)) <= 100
(\at(y, Pre)) <= 100
x - y == 0 ==> x == y
x - y >= (\at(x, Pre) - \at(y, Pre))
x >= \at(x, Pre) && y >= \at(y, Pre)
x - y <= (\at(x, Pre) - \at(y, Pre))
x - 0 == x
y - 0 == y
x - y <= 0 ==> x <= y
x - y >= 0 ==> x >= y
x - x == 0
y - y == 0
x - y == x - y
x - 1 <= 99
y - 1 <= 99
(x - 100) == -(100 - x)
(y - 100) == -(100 - y)
x == y ==> x <= 100
0 <= x - x
0 <= y - y
x >= x - 0
y >= y - 0
x - y == \at(x, Pre) - \at(y, Pre)
x - (\at(x, Pre)) == y - (\at(y, Pre))
(x <= 100) ==> (y <= 100)
x == y == 0 ==> (x <= 0 && y <= 0)
```

## 本次人工设计的复用目标

- H: x−y=x₀−y₀
- H: x≥x₀ ∧ y≥y₀
- E: max(x,y)=100
- E: min(x,y)=100−abs(x₀−y₀)

边界条件只决定至少一个变量到 100；保存的差值进一步决定另一个变量。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
