/*@ requires k >= 0 && k <= 20000001; */
void foo263(int k) {

    int n;
    int i;

    n = 0;
    i = 0;


    while (i < 2 * k) {
       if(i % 2 == 0){
       n = n + 1;
      }
       i = i + 1;
      }

    /*@ assert n == k; */

  }