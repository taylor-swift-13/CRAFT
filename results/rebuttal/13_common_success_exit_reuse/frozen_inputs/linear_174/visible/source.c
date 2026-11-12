/*@ requires x >= 0 && y >= 0 && x == y; */
void foo174(int x, int y) {

    int n;

    n = 0;


    while(x != n){
       x = x - 1;
       y = y - 1;
      }

    /*@ assert (x == n) ==> (y == n); */

  }