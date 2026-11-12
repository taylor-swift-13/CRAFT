int unknown();
/*@ requires x <= -2 && x >= -3; */
void foo201(int x) {



    while(unknown()){
       if(x < 1){
       x = x + 2;
      }
       else if(x >= 1){
       x = x + 1;
      }
      }

    /*@ assert x >= -5; */

  }