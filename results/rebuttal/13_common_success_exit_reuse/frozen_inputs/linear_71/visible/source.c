int unknown();
/*@ requires 0 <= y && y <= 127; */
void foo71(int y) {
    int c = 0;
    int z = 36 * y;
    
  
  
  
    while (unknown()) {

      if ( c < 36 )
      {
      z  = z + 1;
      c  = c + 1;
      }
      
    }

    /*@ assert  ( c < 36 ) ==> ( z < 4608 ) ; */
  
  
  
  }