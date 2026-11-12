void foo255() {

    int v;

    v = 1;


    while (v <= 50) {
       v = v + 2;
      }

    /*@ assert v <= 52; */

  }