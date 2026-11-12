int unknown();
void foo211() {

    int x;
    int y;
    int z;

    x = 0;
    y = 0;
    z = 0;


    while(unknown()){
       x = x + 1;
       y = y + 2;
       z = z + 3;
      }

    /*@ assert z >= 0; */

  }