int unknown();
/*@ requires x <= 1 && x >= 0; */
void foo202(int x) {

    int y;

    y = -3;


    while(unknown()){
       if(x - y < 2){
       x = x - 1;
       y = y + 2;
      }
       else if(x - y >= 2){
       y = y + 1;
      }
      }

    /*@ assert y >= -3; */

  }