/*@ requires x == y && x >= 0 && x + y + z == 0; */
void foo286(int x, int y, int z) {



    while (x > 0) {
       x--;
       y--;
       z += 2;
      }

    /*@ assert z <= 0; */

  }