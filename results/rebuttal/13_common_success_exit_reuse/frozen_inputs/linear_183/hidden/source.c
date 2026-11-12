/*@ requires x != y && t == y; */
void foo183(int t, int x, int y) {

    int i;

    i = 0;


    while(x > 0){
       y = x + y;
      }

    /*@ assert y >= t; */

  }