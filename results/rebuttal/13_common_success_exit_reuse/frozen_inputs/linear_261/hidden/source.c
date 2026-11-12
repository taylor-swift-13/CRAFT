int unknown();
void foo261() {

    int x;

    x = 0;


    while (unknown()) {
       if(x == 0){
       x = 1;
      }
      }

    /*@ assert x >= 0; */

  }