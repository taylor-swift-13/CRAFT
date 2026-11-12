/*@ requires i < j && k > 0; */
void foo292(int i, int j, int k) {



    while (i < j) {
       k = k + 1;
       i = i + 1;
      }

    /*@ assert k > j - i; */

  }