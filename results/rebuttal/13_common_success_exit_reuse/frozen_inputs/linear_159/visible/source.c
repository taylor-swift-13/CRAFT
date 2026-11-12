/*@ requires n > 0 && k > n; */
void foo159(int k, int n) {

    int j;

    j = 0;


    while(j < n){
       j = j + 1;
       k = k - 1;
      }

    /*@ assert (j >= n) ==> (k >= 0); */

  }