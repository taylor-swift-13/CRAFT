# linear_10

## 原始程序（未修改）

```c
int unknown();
/*@ requires x >= 0 && x <= 2 && y <= 2 && y >= 0; */
void foo10(int x,int y) {

  
    while (unknown()) {
      
      x  = x + 2;
      y  = y + 2;

    }
      /*@ assert (y == 0) ==> (x != 4);  */
  
  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：b602224cb700a43a9e6b5b11cf8d80b31a3d8d6ecd2b272fe3a5dcd9cd365fdc。

## 冻结不变量（原样列出）

```text
x - y == \at(x, Pre) - \at(y, Pre)
(x - \at(x, Pre)) == (y - \at(y, Pre))
x <= y ==> x - y <= 0
x >= 0
y >= 0
x >= 0 && y >= 0
(\at(x, Pre) <= x)
(\at(y, Pre) <= y)
x - y >= \at(x, Pre) - \at(y, Pre)
0 <= x - \at(x, Pre)
0 <= y - \at(y, Pre)
x - \at(x, Pre) == 2 * (x - \at(x, Pre)) / 2
y - \at(y, Pre) == 2 * (y - \at(y, Pre)) / 2
0 <= \at(x, Pre)
\at(x, Pre) <= 2
0 <= \at(y, Pre)
\at(y, Pre) <= 2
\at(x, Pre) <= \at(x, Pre) + 2
\at(y, Pre) <= \at(y, Pre) + 2
x >= 2 * 0 + \at(x, Pre)
y >= 2 * 0 + \at(y, Pre)
x % 2 == \at(x, Pre) % 2
y % 2 == \at(y, Pre) % 2
x + y >= 0
0 <= x - x % 2
0 <= y - y % 2
x - 2 * (0) == x
y - 2 * (0) == y
x == x
y == y
x <= 2147483647
y <= 2147483647
y - x == \at(y,Pre) - \at(x,Pre)
```

## 本次人工设计的复用目标

- H: x−y=x₀−y₀
- H: x mod 2=x₀ mod 2
- H: x≥x₀ ∧ y≥y₀
- E: −2≤x−y≤2

入口 x₀,y₀∈[0,2]。摘要保留差值、单调性和同余，原目标只排除一个条件状态。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
