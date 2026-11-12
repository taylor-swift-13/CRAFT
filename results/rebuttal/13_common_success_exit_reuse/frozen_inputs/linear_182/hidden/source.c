/*@ requires n >= 0 && n <= 20000001 && k >= 0 && k <= 20000001; */
void foo182(int k, int n) {

    int i;
    int j;

    j = 0;
    i = 0;


    while(i <= n){
       i = i + 1;
       j = j + 1;
      }

    /*@ assert (i > n) ==> (k + i + j) > (2 * n); */

  }