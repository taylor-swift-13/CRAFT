void foo257() {

    int x;

    x = 40;


    while (x != 0) {
       x = x - 1;
      }

    /*@ assert x == 0; */

  }