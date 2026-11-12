int unknown();
void foo224() {

    int x;

    x = 0;


    while(unknown()){
       x = x + 4;
      }

    /*@ assert x % 4 == 0; */

  }