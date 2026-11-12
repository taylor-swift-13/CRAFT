void foo254() {

    int x;
    int y;

    x = 1;
    y = 1;


    while (y > 0) {
       if (x < 50){
       y++;
      }
       else{
       y--;
      }
       x = x + 1;
      }

    /*@ assert x == 100; */

  }