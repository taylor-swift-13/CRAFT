int unknown();
/*@ requires 0 <= y && y <= 127; */
void foo73(int y) {
  int c = 0;
  int z = 36 * y;
  



  while (unknown()) {

    if ( c < 36 )
    {
    z  = z + 1;
    c  = c + 1;
    }
  }

  /*@ assert  ( z < 0 && z >= 4608) ==> ( c >= 36 ); */

  
}