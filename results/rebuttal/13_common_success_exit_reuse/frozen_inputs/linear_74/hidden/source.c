int unknown();
/*@ requires 0 <= y && y <= 127; */
void foo74(int y,int x1,int x2,int x3) {
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