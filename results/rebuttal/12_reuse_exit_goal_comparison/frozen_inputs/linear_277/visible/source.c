/*@ requires n >= 0 && n <= 20000001; */
void foo277(int n) {

    int i;
    int k;

    i = 0;
    k = n;


    while (i < n) {
       k--;
       i += 2;
      }

    /*@ assert 2 * k >= n - 1; */

  }