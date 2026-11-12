int unknown();
void foo259() {

    int x;

    x = 0;


    while (unknown()) {
       if(unknown()){
       x = x + 1;
       if(x > 40){
       x = 0;
      }
      }
      }

    /*@ assert x <= 40; */

  }