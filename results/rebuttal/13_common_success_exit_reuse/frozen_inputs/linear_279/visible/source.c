/*@ requires i >= 1 && j >= 1 && i * i < j * j; */
void foo279(int i, int j) {



    while (i < j) {
       j = j - i;
       if (j < i) {
       j = j + i;
       i = j - i;
       j = j - i;
      }
      }

    /*@ assert j == i; */

  }