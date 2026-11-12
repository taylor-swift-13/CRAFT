# linear_194

## 原始程序（未修改）

```c
/*@ requires i >= 0; */
void foo194(int i) {

    int x;
    int y;
    int z;

    x = i;
    y = i;
    z = 0;


    while(x != 0){
       x = x - 1;
       y = y - 2;
       z = z + 1;
      }

    /*@ assert y == 0 - z; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：d3b6bc7f5037fdc1cab04f3029fd12f840d288e9fbbbe77f91b046b93169417e。

## 冻结不变量（原样列出）

```text
0 <= i
z >= 0
z == i - x
x >= 0
y >= -i
x + z == i
y + (2 * z) == i
x == i - z
y == i - z - (i - x)
z <= i
i >= 0 ==> x == i - z
y - i == - (i - x) * 2
y >= -2 * i
z <= i && z >= 0
y == i - (i - x) * 2
x <= i
y <= i
x == i - (i - x)
(x == 0) ==> (z == i)
(x >= 0) && (y == i - 2*(i - x))
i >= 0 ==> y == i - 2*(i - x)
i >= 0 ==> x + z == i
i >= 0 ==> y + 2*z == i
y == i - 2*(z)
x - (i - z) == 0
y - (i - 2*(z)) == 0
2*(i) == 2*(x) + 2*(z)
x >= 0 ==> y >= -i
x >= 0 && y <= i*2 && z >= 0
y == i - (i - y)
x >= 0 && z >= 0
y + z <= i
i >= 0 && z >= 0
i >= 0 ==> z == i - x
x <= i && y <= 2*i && z <= i
z == (i - x) && x >= 0
y + 2*(i - x) == i
(i - x) >= 0
y == x * 2 - i * 2 + i
y <= 2 * i
x == i - z && y == i - 2 * z
(i - x) >= 0 ==> z == i - x
x + y + z == 2*i - (i - x) - (i - y) + (i - x)
```

## 本次人工设计的复用目标

- H: x+z=i
- H: y+2z=i
- E: z=i
- E: y=−i

退出 x=0 先确定 z，再确定 y；原目标只约束两个输出之间的关系。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
