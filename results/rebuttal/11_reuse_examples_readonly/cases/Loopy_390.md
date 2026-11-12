# Loopy_390

## 原始程序（未修改）

```c
// Source: data/benchmarks/sv-benchmarks/loop-zilu/benchmark02_linear.c
extern int unknown_int(void);
/*@
  requires l>0;
*/
void loopy_390(int n, int l) {
  
  int i = unknown_int();
  
  i = l;
  
  while (i < n) {
    i++;
  }
  {;
//@ assert(l>=1);
}

  return;
}
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：92b9c654c2edd58438cd50250c5294a8893a54f5980ca993ad720c4ac96a4cf6。

## 冻结不变量（原样列出）

```text
i >= l
i == l || i > l
i - l >= 0
l >= 1
i - l == 0 || i - l > 0
0 <= i
i == l + (i - l)
(i <= n) ==> (i - l) <= (n - l)
(i - l) == 0 ==> i == l
(i - l) == (i - l)
i < n ==> i + 1 <= n
i == l || (i > l && i <= n)
i > l ==> i <= n
i == l + ( (n - l) - (n - i) )
i <= n && l > 0 ==> i <= n
i <= n && l > 0 ==> i >= l
```

## 本次人工设计的复用目标

- H: i≥l
- H: i=l ∨ i≤n
- E: i=max(l,n)
- E: i−l=max(n−l,0)

原目标仅重述入口条件。结合退出 i≥n，可完整描述循环对 i 的作用。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
