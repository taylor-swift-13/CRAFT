# Loopy_58

## 原始程序（未修改）

```c
// Source: data/benchmarks/LinearArbitrary-SeaHorn/loops/loop-lit/gr2006_true-unreach-call_true-termination.c

void loopy_58(void) {
    int x, y;
    x = 0;
    y = 0;
    while (1) {
        if (x < 50) {
            y++;
        } else {
            y--;
        }
        if (y < 0) break;
        x++;
    }
    {;
//@ assert(x == 100);
}

    return;
}
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：7c20df1ba2a9fc3c8baba8a13d4576f40aed48bb1c6a05349be3068667f78e58。

## 冻结不变量（原样列出）

```text
x >= 0
y >= 0
x <= 50 || (x > 50 ==> y >= 0)
(x <= 50) ==> (y == x)
(x >= 0) ==> (y >= 0)
x <= 50 || x > 50
x >= 0 && y >= 0
x + y >= 0
(x < 50) ==> (y == x)
y >= 0 && x >= 0
y <= x
(x <= 50) ==> ((y == x) || (y == x - 1) || (y == x - 2) || (y == x - 3) || (y == x - 4) || (y == x - 5))
(x == 0) ==> (y == 0)
(x == 50) ==> (y >= 0)
(y >= 0) ==> (x >= 0)
(x >= 0) && (y >= 0) && (y <= x)
x <= 100
x > 50 ==> y == 100 - x
0 <= x && x <= 50 || x > 50
y <= 100 - (x - 0)
(x < 50) ==> (y >= 0)
(x >= 50) ==> (y >= 0)
y >= 0 ==> x <= 100
y == 0 ==> x >= 0
(y > 0) ==> (x > 0)
(x <= 50) ==> (y >= 0)
x > 50 ==> y == 50 - (x - 50)
x >= 0 && y >= 0 && (x <= 50 ==> y == x)
x <= 50 ==> y <= x
x + y <= 100
x <= 50 ==> y == y
(x >= 50) ==> (y == 50 - (x - 50) )
(x <= 50) && (y >= 0) ==> (y <= x)
```

## 本次人工设计的复用目标

- H: y=min(x,100−x)
- H: 0≤y≤50
- E: y=−1
- E: x=100 ∧ y=−1

break 在 y 更新后、x 更新前发生。循环头 x=100,y=0，经 y-- 后退出；不能把循环头 y≥0 错用到退出点。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
