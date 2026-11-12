void foo236() {

    unsigned int x;

    x = 268435440;


    while (x > 0) {
       x -= 2;
      }

    /*@ assert x % 2 == 0; */

  }