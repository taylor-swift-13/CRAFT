/*@ requires x < y; */
void foo280(int x, int y) {



    while (x < y) {
       x = x + 100;
      }

    /*@ assert x >= y; */

  }