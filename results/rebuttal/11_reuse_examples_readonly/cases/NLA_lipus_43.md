# NLA_lipus_43

## 原始程序（未修改）

```c
/*@ requires a % 2 == 0 && a > 0;*/
int main43(int a){
  
  int r = 0;
  int x = a / 2;

  while (x > r){
        x = x - r;
        r = r + 1;
    }
  /*@ assert (r+1) * (r+1) >= a;*/
}


```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：047cc1b98c03d11d27a297f9aba9342a9f372a9800f03bcd1989694037b58c62。

## 冻结不变量（原样列出）

```text
0 <= r
x >= 0
r <= a / 2
x > r ==> (x - r) > 0
r <= a / 2 && x >= 0
0 <= a / 2 - r
0 <= a
(x - r) + (r) == x
0 <= r && r <= a / 2
a > 0
x == a / 2 - (r - 1) * r / 2
(x - r) >= 0 ==> x >= r
(a % 2 == 0) ==> (2 * r <= a)
x > 0 ==> r < a / 2 + 1
a == 2 * x + r * (r - 1)
x <= a / 2
a == 2 * (a/2)
x >= 0 && r >= 0
a % 2 == 0
r <= a
x > r ==> x - r == (a / 2) - (r * (r + 1)) / 2
x - (a / 2) == -(r * (r - 1)) / 2
(r < a) ==> (x > 0)
2 * r <= a
x > r || !(x > r)
0 <= a / 2
(r*(r-1))/2 <= a/2
a == 2*(x + (r*(r-1))/2)
(x >= 0) ==> ((r*(r-1))/2 <= a/2)
r*(r-1) <= a - 1
```

## 本次人工设计的复用目标

- H: a=2x+r(r−1)
- H: r(r−1)<a
- E: a≤r(r+1)
- E: 0≤x≤r

退出 x≤r，代入保存的代数关系得到更紧的上下夹逼，而不只原始粗上界。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
