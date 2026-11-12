# NLA_lipus_32

## 原始程序（未修改）

```c

int main32(int a){

  int sn = 0;
  int x = 0;

  while(unknown()) {
    if (x<10) {
        sn = sn + a;
        x++;
    }
  }
  /*@ assert sn == x*a || sn == 0;*/
}


```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：d181c4ac87dbf0930409fdbba1b7d1be13a1ea3e5a0c14cf130b913fdec88628。

## 冻结不变量（原样列出）

```text
0 <= x
x <= 10
sn == (x * a)
(x <= 10) ==> (sn == x * a)
(sn >= 0) ==> (x >= 0)
sn - x * a == 0
0 <= x && x <= 10
a * x - sn == 0
x <= 10 && sn == a * x
(x < 10) ==> (sn == a * x)
(x >= 0) && (sn == a * x)
(a == 0) ==> (sn == 0)
(a != 0) ==> (sn == a * x)
x <= 10 || !(x <= 10) ==> (sn == a * x)
(x == 0) ==> (sn == 0)
(x > 0) ==> (sn == (x * a))
sn == x * a <==> x >= 0
x <= 10 ==> (sn + a * (10 - x) == a * 10)
x <= 10 ==> sn <= a * x
a == a
0 <= sn - a * x
sn == a * (x / 1) - a * (x - (x / 1))
x == 0 || sn == a * x
x >= 0 ==> sn == a * x
sn - a * x == 0 ==> (x <= 10)
x <= 10 && x >= 0
x <= 11 ==> sn == a * x
(sn == a * x) ==> (x <= 10)
sn == (x * a) - (x * a - sn)
sn <= x * a
(sn - x * a) <= 0
x == 0 || x >= 1
```

## 本次人工设计的复用目标

- H: 0≤x≤10
- H: sn=a·x
- E: a>0⇒sn≥0
- E: a≠0⇒(sn=0 ⇔ x=0)

精确乘法关系去掉原目标的宽松析取分支；范围和代数关系分别可复用。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
