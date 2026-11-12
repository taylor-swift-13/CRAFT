# Loopy_185

## 原始程序（未修改）

```c
// Source: data/benchmarks/accelerating_invariant_generation/dagger/substring1.c

/*@
  requires k >= 0;
  requires k <= 100;
  requires from >= 0;
  requires from <= k;
*/
void loopy_185(int from, int to, int k) {
int i, j;












i = from;
j = 0;

while (i < k) {
i++;
j++;
}

{;
//@ assert(j <= 100);
}

}

```

## 原有证据

已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：3f58de5374f401b617e5a7df827bc22846346365805426c36b002daf7424fc93。

## 冻结不变量（原样列出）

```text
i == from + j
j >= 0
i >= from
i <= k
j <= k - from
i - from == j
i <= k && j <= k - from ==> i - from == j
i >= from && i <= k
i <= k || (i == k + 1 && j == k - from + 1)
i <= k && j <= k - from
i - from >= 0
i - from <= k - from
i - from >= 0 ==> j >= 0
i <= k && j <= k - from && i >= from
0 <= i
j >= 0 ==> i >= from
i <= k ==> j <= k - from
from <= i == from + j
j <= k
0 <= from
i - from == j && i <= k
i >= from && j >= 0
i - from == j && i <= k && j <= k
j >= 0 && j <= k - from ==> i - from == j
k <= 100
from <= k
(i < k) ==> (j < k - from)
```

## 本次人工设计的复用目标

- H: i=from+j
- H: from≤i≤k
- E: i=k
- E: j=k−from

入口 0≤from≤k≤100；退出时 i≥k，恢复精确终点和区间长度。

这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。
