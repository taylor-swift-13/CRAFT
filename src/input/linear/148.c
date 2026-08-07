int unknown();
void foo148() {

    int x;
    int y;

    x = 1;
    y = 1;


    while(unknown()){
       x = x + y;
       y = x + y;
      }

    /*@ assert y >= 1; */

  }
