/*@ requires y >= 0 && y <= 20000001; */
void foo284(int y) {

    int x;

    x = 4 * y;


    while (x > 0) {
       x -= 4;
       y--;
      }

    /*@ assert y >= 0; */

  }