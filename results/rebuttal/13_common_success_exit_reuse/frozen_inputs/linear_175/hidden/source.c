/*@ requires n > 0; */
void foo175(int n) {

    int i;
    int c;

    i = 0;
    c = 0;


    while(i < n){
       c = c + i;
       i = i + 1;
      }

    /*@ assert (i >= n) ==> (c >= 0); */

  }