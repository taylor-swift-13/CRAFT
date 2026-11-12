/*@ requires y >= 0; */
void foo270(int y) {

    int x;

    x = y;


    while (x != 0) {
       x = x - 1;
       y = y - 1;
      }

    /*@ assert y == 0; */

  }