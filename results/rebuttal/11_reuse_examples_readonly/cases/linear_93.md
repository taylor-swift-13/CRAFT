# linear_93

## 原始程序（未修改）

```c
int unknown();

/*@ requires n >= 0; */
void foo93(int n) {
  
    int i = 0;
    int x = 0;
    int y = 0;
    
  
    while (i < n) {
      
        i  = i + 1;
        if (unknown()) {
          
          x  = x + 1;
          y  = y + 2;
          
        } else {
          
          x  = x + 2;
          y  = y + 1;
          
        }
  
  
    }
  
    /*@ assert (3 * n) == (x + y); */
  
  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：f7d0cc91c126b06a739d14b384e78cb3a50fe8e4bb608628315f996854930a4f。

## 冻结不变量（原样列出）

```text
0 <= i
i <= n
x >= 0
y >= 0
0 <= i && i <= n
i <= n ==> x + y == 3 * i
x <= 2 * i
y <= 2 * i
(i > 0) ==> (x + y >= 3 * (i))
x + y == i + (x + y) - i
x + y == 3 * i
(i == 0) ==> (x == 0 && y == 0)
(i > 0) ==> (x >= 0 && y >= 0)
x + y <= 3 * i
x + y >= 3 * i
x + y - 3 * i == 0
i > 0 ==> (x + y == 3 * i)
i < n ==> (x + y == 3 * i)
(0 <= i) && (i <= n) ==> (x + y == 3*i)
(i >= 0) ==> (x == 0 || x >= 0)
(i >= 0) ==> (y == 0 || y >= 0)
x + y == 3 * i - (i - i)
```

## 本次人工设计的复用目标

- H: x+y=3i
- H: 0≤x,y≤2i
- E: n≤x≤2n
- E: n≤y≤2n
- E: abs(x−y)≤n
- E: i=n

退出 i=n；总和结合每一分量上界导出另一分量下界，得到原总和目标未包含的分量范围。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
