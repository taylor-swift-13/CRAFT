void foo305() {

    int x;

    x = 0;


    while (x < 1000000) {
       if (x < 750000) {
       x++;
      }
       else{
       x = x + 2;
      }
      }

    /*@ assert x == 1000000; */

  }