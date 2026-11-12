# Loopy_123

## 原始程序（未修改）

```c
// Source: data/benchmarks/LinearArbitrary-SeaHorn/pie/hola/10.c

extern int unknown2();

void loopy_123(void) {

	int w = 1;
	int z = 0;
	int x= 0;
	int y=0;

         while(unknown2()){
	    if(w) {
		x++; 
		w=!w;
	    };
	    if(!z) {
		y++; 
		z=!z;
	    };
	}

	{;
//@ assert(x==y);
}

}
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：ee3ceac4d261e658dfd8d031f0d05e7a528f01be21016f85c520207e80ef8e08。

## 冻结不变量（原样列出）

```text
w == 1 || w == 0
z == 0 || z == 1
x >= 0
y >= 0
x + y <= 2 * (w + z) + 0
w + z <= 2
x <= 1
y <= 1
w <= 1
z <= 1
0 <= x <= 1
0 <= y <= 1
w == 1 ==> x == 0
w == 0 ==> x >= 0
z == 0 ==> y >= 0
w + z == 1
(w == 1) <==> (x == 0)
w == 0 || w == 1
x + y <= 2
x <= 1 || y <= 1
x - y == 0
(w == 1) ==> (x == y)
(z == 0) ==> (y == x % 2)
(w == 0) ==> (x >= 1 ==> x == y)
w + z <= 1
x <= y + 1
y <= x + 1
x <= z + w
x <= 1 + x
y <= 1 + y
0 <= w <= 1
0 <= z <= 1
(z == 0) ==> (y == 0)
x <= 1 && y <= 1
z == 0 ==> y - x == 0
x == 0 || x == 1
y == 0 || y == 1
z * y == y
w + z == 1 || w == z
(w == 1) ==> (x == x)
```

## 本次人工设计的复用目标

- H: x+w=1
- H: y=z
- H: w+z=1
- E: x,y∈{0,1}

冻结摘要描述计数器与控制标志的对应关系，而不仅是两个计数器相等。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
