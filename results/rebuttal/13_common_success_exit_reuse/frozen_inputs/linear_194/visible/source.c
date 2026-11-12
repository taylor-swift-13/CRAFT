/*@ requires i >= 0; */
void foo194(int i) {

    int x;
    int y;
    int z;

    x = i;
    y = i;
    z = 0;


    while(x != 0){
       x = x - 1;
       y = y - 2;
       z = z + 1;
      }

    /*@ assert y == 0 - z; */

  }