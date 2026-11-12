int unknown();

int foo87(int y) {

    int lock = 1;
    int x = y;

    while (x != y) {
        if ( unknown() ) {
          
          lock  = 1;
          x  = y;
          
        } else {
          
          lock  = 0;
          x  = y;
          y  = y + 1;
          
        }
 
    }
    /*@ assert lock == 1;*/
  }