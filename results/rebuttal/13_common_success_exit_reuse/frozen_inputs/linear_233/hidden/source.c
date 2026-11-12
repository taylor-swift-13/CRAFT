void foo233() {

    unsigned int x;

    x = 0;


    while (x < 268435455) {
       x += 2;
      }

    /*@ assert x % 2 == 0; */

  }