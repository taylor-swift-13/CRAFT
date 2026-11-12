int unknown();
/*@ requires k >= 0 && k <= 1; */
void foo251(int k) {

    int i;
    int j;

    i = 1;
    j = 1;


    while (unknown()) {
       i = i + 1;
       j = j + k;
       k = k - 1;
      }

    /*@ assert i >= 1; */

  }