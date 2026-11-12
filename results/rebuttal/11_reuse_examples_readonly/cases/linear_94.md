# linear_94

## 原始程序（未修改）

```c
/*@ requires n >= 0; 
    requires k >= 0;
*/
void foo94(int n,int k) {
  
    int i = 0;
    int j = 0;
    
    
    while (i <= n) {
      
       i  = i + 1;
       j  = j + i;
      
  
    }
    /*@ assert i + j + k > (2 * n); */
  
  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：3bc9df7882ac26d09abe071e647991ac4f5bd06e27e4eaedd10db2a75bda7c9a。

## 冻结不变量（原样列出）

```text
0 <= i
0 <= j
i <= n + 1
j == i * (i + 1) / 2
0 <= i <= n + 1
0 <= i <= n + 1 && 0 <= j
n >= 0 && k >= 0 ==> i <= n + 1
j <= (n + 1) * (n + 2) / 2
k >= 0
i == j - 1 || (i >= 1 ==> j == i*(i+1)/2)
n >= 0
(i >= 0) ==> (j >= 0)
(i == 0) ==> (j == 0)
n >= 0 ==> i >= 0
k >= 0 ==> j >= 0
(i - 1) * i / 2 <= j
(i * (i + 1)) / 2 <= j
n >= i - 1
(i * (i + 1)) == 2 * j
i >= 0 && j >= 0
i <= n + 1 && j <= (n + 1) * (n + 2) / 2
j == (i - 1) * i / 2 + i
(i * (i + 1)) % 2 == 0
i + j >= 0
j - i*(i+1)/2 == 0
0 <= j <= (n + 1) * (n + 2) / 2
i + n + 1 >= 1
i + 1 == (n + 1) - (n - i + 1) + 1
j == 0 ==> i == 0
```

## 本次人工设计的复用目标

- H: 2j=i(i+1)
- H: 0≤i≤n+1
- E: i=n+1
- E: 2j=(n+1)(n+2)

退出 i>n 后确定迭代次数和精确和式，远比原不等式提供的信息多。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
