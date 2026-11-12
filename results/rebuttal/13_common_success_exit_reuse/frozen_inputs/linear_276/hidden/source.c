void foo276() {

    int i;
    int j;

    i = 0;
    j = 0;


    while (i < 100) {
       j += 2;
       i++;
      }

    /*@ assert j == 200; */

  }