/*@ requires x < 100 && y < 100; */
void foo287(int x, int y) {



    while (x < 100 && y < 100) {
       x = x + 1;
       y = y + 1;
      }

    /*@ assert x == 100 || y == 100; */

  }