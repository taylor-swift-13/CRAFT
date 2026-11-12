void foo285() {

    int x;
    int y;
    int z;

    x = 0;
    y = 0;
    z = 0;


    while (x > 0) {
       x++;
       y++;
       z -= 2;
      }

    /*@ assert x + y + z == 0; */

  }