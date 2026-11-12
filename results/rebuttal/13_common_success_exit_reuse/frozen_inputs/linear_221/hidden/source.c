int unknown();
/*@ requires x > 0 && y > 0 && z > 0 && w > 0; */
void foo221(int w, int x, int y, int z) {


    x = w;
    z = y;


    while(unknown()) {
       if (unknown()) {
       w = w + 1;
       x = x + 1;
      }
       else {
       y = y - 1;
       z = z - 1;
      }
      }

    /*@ assert y == z; */

  }