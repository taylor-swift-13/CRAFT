/*@ requires n >= 1 && n <= 1000; */
void foo262(int n) {

    int sum;
    int i;

    sum = 0;
    i = 0;


    while (i < n) {
       sum = sum + i;
       i = i + 1;
      }

    /*@ assert 2 * sum == n * (n - 1); */

  }