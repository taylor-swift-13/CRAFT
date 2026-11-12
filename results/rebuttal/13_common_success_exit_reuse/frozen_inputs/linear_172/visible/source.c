/*@ requires n > 0; */
void foo172(int n) {

    int i;
    int sum;

    sum = 0;
    i = 0;


    while(i < n){
       sum = sum + i;
       i = i + 1;
      }

    /*@ assert (i >= n) ==> (sum >= 0); */

  }