# linear_209

## 原始程序（未修改）

```c
int unknown();
void foo209() {

    int x;
    int y;
    int z;

    x = 0;
    y = 0;
    z = 0;


    while(unknown()){
       x = x + 1;
       y = y + 2;
       z = z + 3;
      }

    /*@ assert x >= 0; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：86aabb5b6852da913255eac10323d573054a004f8154e6ba48bd9242726cf9fd。

## 冻结不变量（原样列出）

```text
x >= 0
y >= 0
z >= 0
y == 2 * x
z == 3 * x
x + 1 > 0 ==> y - 2 * x == 0
x + 1 > 0 ==> z - 3 * x == 0
x == y / 2
x == z / 3
x - y == -x
y - z == -x
z - 3 * x == 0
0 <= x && 0 <= y && 0 <= z
x + y + z == 6 * x
x <= x + 1
y <= y + 2
z <= z + 3
x == 0 ==> y == 0
y == 0 ==> z == 0
z == 0 ==> x == 0
(x + y + z) % 6 == 0
x <= y <= z
x <= x
z == x + y
5 * x - y - z == 0
x + 0 <= y
y + 0 <= z
z - y == x
z - x == z - x
2*x - y == 0
x == 0 || x >= 1
y == 0 || y >= 2
z == 0 || z >= 3
x == 0 || (x > 0)
y == 0 || (y > 0)
z == 0 || (z > 0)
x + y + z == 0 || x + y + z > 0
y - 2 * x == 0
x <= z / 3
y <= 2 * x
z <= 3 * x
```

## 本次人工设计的复用目标

- H: y=2x
- H: z=3x
- E: z=x+y
- E: 3y=2z
- E: x≤y≤z

原目标只看 x 的符号；冻结摘要还刻画了三个计数器间的精确比例。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
