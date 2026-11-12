/*@ requires n >= 1 && k >= n; */
void foo267(int k, int n) {

    int j;

    j = 0;


    while (j <= n - 1) {
       j = j + 1;
       k = k - 1;
      }

    /*@ assert k >= 0; */

  }