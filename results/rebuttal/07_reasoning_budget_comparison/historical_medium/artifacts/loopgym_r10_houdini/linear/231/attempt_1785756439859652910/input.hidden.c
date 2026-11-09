void foo231() {

    unsigned int x;

    x = 0;


    while (x < 268435455) {
       if (x < 65521) {
       x++;
      }
       else {
       x += 2;
      }

      }

    

  }