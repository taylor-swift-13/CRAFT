/*@ requires i >= 0 && i <= 200; */
void foo273(int i) {



    while (i > 0) {
       i = i - 1;
      }

    /*@ assert i >= 0; */

  }