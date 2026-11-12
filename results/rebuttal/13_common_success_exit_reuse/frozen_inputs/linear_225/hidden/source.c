int unknown();
void foo225() {

    int x;

    x = 1;


    while(unknown()){
       x = x + 2;
      }

    /*@ assert x % 2 == 1; */

  }