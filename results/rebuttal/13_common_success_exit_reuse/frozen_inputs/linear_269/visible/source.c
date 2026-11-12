/*@ requires n >= 0; */
void foo269(int n) {

    int i;
    int sum;

    sum = 0;
    i = 0;


    while (i < n) {
       sum = sum + i;
       i = i + 1;
      }

    /*@ assert sum >= 0; */

  }