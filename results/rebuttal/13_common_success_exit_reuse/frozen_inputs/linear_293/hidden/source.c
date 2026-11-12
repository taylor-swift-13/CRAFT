/*@ requires r > i + j; */
void foo293(int i, int j, int r) {



    while (i > 0) {
       i = i - 1;
       j = j + 1;
      }

    /*@ assert r > i + j; */

  }