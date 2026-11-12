/*@ requires x < y && y <= 20000001; */
void foo291(int x, int y) {



    while (x < y) {
       if (x < 0){
       x = x + 7;
      }
       else {
       x = x + 10;
      }
       if (y < 0){
       y = y - 10;
      }
       else {
       y = y + 3;
      }

      }

    /*@ assert x >= y; */

  }