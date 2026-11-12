/*@ requires k > i - j && i < j; */
void foo278(int i, int j, int k) {



    while (i < j) {
       k = k + 1;
       i = i + 1;
      }

    /*@ assert k > 0; */

  }