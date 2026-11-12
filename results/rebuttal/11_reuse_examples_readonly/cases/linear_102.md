# linear_102

## 原始程序（未修改）

```c
void foo102(int n) {
  
    int x = 0;
    
    
    while (x < n) {
      
      x  = (x + 1);
      
    }
    /*@ assert (n >= 0) ==> (x == n); */
    
  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：6d79d9f43211af3931823ad90732e500d777a2f3d23025c29db53075cfebffab。

## 冻结不变量（原样列出）

```text
0 <= x
x == 0 || x > 0
x + (n - x) == n
(x < n) ==> (x + 1 <= n)
(x < n) ==> (x + 1 == x + 1)
n == n
x != -1
x == (x + 1) - 1
x == x
n >= 0 ==> x <= n
x == n * 0 + x
(x == n) ==> (x <= n)
(x < n) ==> (n - x) > 0
x == n - (n - x)
x + 1 <= n ==> x < n
x - (n - x) == 2 * x - n
(x <= n) ==> (x >= 0)
x - x == 0
1 <= x ==> x <= n
(x + 1) >= 1
x == 0 ==> n == n
x == (x / 1) * 1
(n - x) % 1 == 0
```

## 本次人工设计的复用目标

- H: x≥0
- H: x>0⇒x≤n
- E: x=max(n,0)
- E: n<0⇒x=0

负参数分支中 x>0 不可能，因此 x=0；补全原条件目标没有规定的行为。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
