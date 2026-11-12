/*@ requires n > 0; */
void foo282(int n) {

    int i;
    int k;

    i = 0;
    k = n;


    while (i < n && n > 0) {
       k--;
       i++;
      }

    /*@ assert k == 0; */

  }