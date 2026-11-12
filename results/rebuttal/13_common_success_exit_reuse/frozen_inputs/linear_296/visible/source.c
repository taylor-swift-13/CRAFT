void foo296() {

    unsigned int x;
    unsigned int y;

    x = 0;
    y = 0;


    while (x < 1000000) {
       if (x < 500000) {
       y++;
      }
       else {
       y--;
      }
       x++;
      }

    /*@ assert y == 0; */

  }