# linear_78

## 原始程序（未修改）

```c

int unknown();
/*@ requires x >= y  && y >= 0; */
void foo78(int x, int y) {

    int i = 0;
    
    
    while (unknown()) {
      if ( i < y )
      {
      i  = (i + 1);
      }
  
    }
    
  /*@ assert (i < y) ==> (i >= 0); */
    
}
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：9e5e02de464a4e10307672b967df781d5e79902b2b75b876734601456191b62d。

## 冻结不变量（原样列出）

```text
0 <= i
i <= y
0 <= i && i <= y ==> i == (i / 1) * 1
i <= y ==> i >= 0
0 <= i && i <= y && y >= 0
(i <= y) ==> (i + (y - i) >= y)
(i < y) ==> (i + 1 <= y)
(i == y) || (i < y)
0 <= y
i <= y && y >= 0
(i == y) ==> (i <= y)
y - i >= 0
i == y ==> (i == y)
(i <= y) ==> (i <= y)
(i < y) ==> (i <= y)
i <= y && 0 <= i
i == (0) + i
0 <= i && i <= y
i == y ==> i >= 0
(i == 0) ==> (i >= 0)
(i >= 0) ==> (i <= y)
x >= y
i <= y ==> i == y || i < y
(i + (y - i)) == y
x >= y && y >= 0 ==> x >= i
i == y ==> !(i < y)
i == 0 || i > 0
(i >= 0) ==> (i <= y) ==> (i <= y)
i == 0 || i <= y
(i == 0) ==> (i <= y)
(i < y) ==> (i == i)
(i < y) ==> (i + 1 == i + 1)
i == 0 || i == 1 || i >= 2
(i + 1) <= y ==> i < y
i == y * 0 + i
(i > 0) ==> (i <= y)
i <= y && (i == y ==> i >= 0)
```

## 本次人工设计的复用目标

- H: 0≤i≤y
- E: i≤x
- E: y=0⇒i=0
- E: i(i−y)≤0

范围摘要同时限制上界和下界；其中上界及与 x 的关系并非原目标所给出的信息。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
