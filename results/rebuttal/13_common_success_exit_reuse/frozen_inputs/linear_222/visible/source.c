int unknown();
/*@ requires x > 0 && y > 0 && z > 0 && w > 0; */
void foo222(int w, int x, int y, int z) {


    x = w;
    z = x + 1;
    y = w + 1;


    while (unknown()) {
       y = y + 1;
       z = z + 1;
      }

    /*@ assert y == z; */

  }
