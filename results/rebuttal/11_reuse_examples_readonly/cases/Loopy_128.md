# Loopy_128

## 原始程序（未修改）

```c
// Source: data/benchmarks/LinearArbitrary-SeaHorn/pie/hola/16.c

extern int unknown1();

void loopy_128(int i, int j) {

  
  
  
  int x = i;
  int y = j;
 
  while(x!=0) {
	x--;
	y--;
  }
  if(i==j)
	{;
//@ assert(y==0);
}

}

```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：1d6b22e25657e238748f003788e9dd54072f395c2048dcff4243fae4f28001f2。

## 冻结不变量（原样列出）

```text
i == \at(i, Pre)
j == \at(j, Pre)
x - y == i - j
x == i - (i - x)
y == j - (j - y)
(i - x) == (j - y)
x <= i
y <= j
(i - x) >= 0
(j - y) >= 0
x == 0 ==> i >= 0
y == 0 ==> j >= 0
(i - x) >= 0 && (j - y) >= 0
x <= i && y <= j
x - y <= i - j
x - y >= i - j
x == i - (j - y)
y == j - (i - x)
(y == 0) ==> (x == i - j)
x + y == i + j - 2 * (i - x)
i - x == i - x
j - y == j - y
(x == 0) ==> (y == j - i + x)
(x == 0) ==> (y == j - i)
(x >= 0) ==> (y <= j - (i - x))
(x + y) <= i + j
(i >= 0) ==> (x >= 0)
x <= y + (i - j)
(x != 0) ==> (y != j - i)
(x == 0) <==> (y == j - i)
y - x == j - i
(i - x) >= 0 ==> (j - y) >= 0
x - (i - j) == y
i != j ==> x - y <= i - j
```

## 本次人工设计的复用目标

- H: x−y=i−j
- H: x≤i ∧ y≤j
- E: y=j−i
- E: y=0 ⇔ i=j

正常退出 x=0，差值关系给出所有 i,j 下的输出，而不只相等输入这一种条件。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
