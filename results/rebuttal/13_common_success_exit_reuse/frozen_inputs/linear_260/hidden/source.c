int unknown();
void foo260() {

    int x;

    x = 0;


    while (unknown()) {
       if(x == 0){
       x = 1;
      }
      }

    /*@ assert x <= 1; */

  }