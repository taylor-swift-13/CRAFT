int unknown();
void foo223() {

    int x;

    x = 0;


    while(unknown()){
       x = x + 2;
      }

    /*@ assert x % 2 == 0; */

  }