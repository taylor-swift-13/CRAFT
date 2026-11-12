/*@ requires n > 0; */
void foo178(int n) {

    int x;

    x = 0;


    while(x < n){
       x = x + 1;
      }

    /*@ assert (x >= n) ==> (x == n); */

  }