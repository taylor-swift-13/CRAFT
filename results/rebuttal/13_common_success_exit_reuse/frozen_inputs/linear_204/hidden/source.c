int unknown();
/*@ requires x <= 5 && x >= 4 && y <= 5 && y >= 4; */
void foo204(int x, int y) {



    while(unknown()){
       if(x >= 0 && y >= 0){
       x = x - 1;
      }
       else if(x < 0 && y >= 0){
       y = y - 1;
      }
       else if(y < 0){
       x = x + 1;
       y = y - 1;
      }
      }

    /*@ assert y <= 5; */

  }