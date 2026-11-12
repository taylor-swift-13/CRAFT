int unknown();
void foo283() {

    int x;
    int y;

    x = 0;
    y = 0;


    while (unknown()) {
       x += 4;
       y++;
      }

    /*@ assert x == 4 * y; */

  }