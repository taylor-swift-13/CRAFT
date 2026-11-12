int unknown();
void foo264() {

    int x;
    int y;

    x = 1;
    y = 1;


    while (unknown()) {
       x = x + y;
       y = x;
      }

    /*@ assert y >= 1; */

  }