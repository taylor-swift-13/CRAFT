void foo237() {

    unsigned int i;
    unsigned int j;
    unsigned int k;

    i = 0;
    j = 0;
    k = 0;


    while (k < 268435455) {
       i = i + 1;
       j = j + 2;
       k = k + 3;
      }

    /*@ assert k == (i + j); */

  }