# linear_284

## 原始程序（未修改）

```c
/*@ requires y >= 0 && y <= 20000001; */
void foo284(int y) {

    int x;

    x = 4 * y;


    while (x > 0) {
       x -= 4;
       y--;
      }

    /*@ assert y >= 0; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：ade4a49db759a397edb8901e36fb9623951ab01ee620be69ee0a933be0851e4e。

## 冻结不变量（原样列出）

```text
x == 4 * y
y >= 0
x >= 0
y <= 20000001
y <= 20000001 && y >= 0
x >= 0 && y >= 0
x == 4 * y && y >= 0
x == 4 * y && y <= 20000001
y >= 0 ==> x == 4 * y
y == 0 || y > 0
(x % 4) == 0
x == 4 * y && y >= 0 && x >= 0
x >= 0 && y >= 0 && x == 4 * y
x <= 4 * 20000001
y >= 0 ==> x >= 0
0 <= y <= 20000001
x <= 4 * y + 0
(4 * y) - x == 0
(x / 4) == y
(x - 4 * y) == 0
y >= 0 && x >= 0
x > 0 ==> y > 0
y <= \at(y, Pre)
x <= 4 * \at(y, Pre)
x >= 0 ==> y >= 0
y == 0 ==> x == 0
x > 0 ==> (x - 4) >= 0
```

## 本次人工设计的复用目标

- H: x=4y
- H: 0≤y≤y₀
- E: x=0
- E: y=0

退出 x≤0 与 x=4y、y≥0 联合确定两个变量均为零。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
