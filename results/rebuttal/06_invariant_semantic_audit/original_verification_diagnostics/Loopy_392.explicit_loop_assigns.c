// Source: data/benchmarks/sv-benchmarks/loop-zilu/benchmark04_conjunctive.c
extern int unknown_int(void);
/*@
  requires n>=1 && k>=n && j==0;
*/
void loopy_392(int k, int j, int n) {
  
  
  
  
  
  /*@
      loop assigns j, k;
loop invariant j >= 0;
      loop invariant j <= n;
      loop invariant n == n;
      loop invariant n >= 1;
      loop invariant k == k;
      loop invariant k == \at(k, Pre) - j;
      loop invariant j - 0 == j;
      loop invariant 0 <= j <= n;
      loop invariant j - 1 <= n - 1;
    */
    while (j<=n-1) {
    j++;
    k--;
  }
  {;
//@ assert(k>=0);
}

  return;
}