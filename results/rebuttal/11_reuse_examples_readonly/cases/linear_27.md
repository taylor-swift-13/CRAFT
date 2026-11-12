# linear_27

## 原始程序（未修改）

```c
void foo27(int n) {

    int x = n;
    
    
    while (x > 1) {
      
       x  = x - 1;
  
    }
  
   /*@ assert (n >= 1) ==> (x == 1); */
  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：b5fce24fee5f3d54ed4699c07213765cc88b6e2a4ffd3723c1cff3485724832b。

## 冻结不变量（原样列出）

```text
x <= n
0 <= n - x
x > 1 ==> (n - x) < n
n - x == 0 || n - x >= 0
x - 0 == n - (n - x)
x + (n - x) == n
x > 0 ==> x >= 1
(x - 1) == (n - (n - x)) - 1
n >= 1 ==> x <= n
x - 1 <= n - 1
(x > 1) ==> (x - 1 >= 1)
n >= 1 ==> x >= 1
(n - x) % 1 == 0
n > 1 ==> x >= 1
n - x > 0 ==> x > 0
n == \at(n, Pre)
n == n
(n - x) == 0 ==> x == n
x > 1 ==> x >= 1
(n - x) == (n - x)
x > 0 ==> (x - 1) >= 0
x >= 1 ==> x <= n
x > 1 ==> x >= 2
```

## 本次人工设计的复用目标

- H: x≤n
- H: n≥1⇒x≥1
- E: x=min(n,1)
- E: n≤0⇒x=n

摘要还刻画原目标没有约束的 n≤0 分支：若 x<n≤0，则与 n−x>0⇒x>0 矛盾。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
