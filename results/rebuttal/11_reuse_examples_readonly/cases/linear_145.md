# linear_145

## 原始程序（未修改）

```c
/*@ requires mid > 0; */
void foo145(int mid) {

    int lo;
    int hi;

    lo = 0;
    hi = 2 * mid;


    while(mid > 0){
       lo = lo + 1;
       hi = hi - 1;
       mid = mid - 1;
      }

    /*@ assert lo == hi; */

  }
```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：d7aaa0752772378e3bbbe9800e45e51786868a6b02251978987b6831a483f070。

## 冻结不变量（原样列出）

```text
lo >= 0
hi >= 0
mid >= 0
hi - lo == 2 * (mid - 0)
lo + hi == 2 * \at(mid, Pre)
lo <= 2 * \at(mid, Pre) - 0
mid <= \at(mid, Pre)
hi >= lo
\at(mid, Pre) > 0 ==> lo <= 2 * (\at(mid, Pre) - mid) + lo
mid == (hi - lo) / 2
mid >= 0 ==> hi >= 0
hi >= mid
mid == \at(mid, Pre) - (lo - 0)
lo <= lo + hi
```

## 本次人工设计的复用目标

- H: hi−lo=2mid
- H: lo+hi=2m₀
- E: lo=m₀
- E: hi=m₀
- E: mid=0

m₀ 为入口 mid。退出与非负性给出 mid=0，和、差两个关系确定 lo 与 hi。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
