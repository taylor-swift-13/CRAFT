void foo235() {

    unsigned int x;

    x = 268435441;


    while (x > 1) {
       x -= 2;
      }

    /*@ assert x % 2 == 1; */

  }