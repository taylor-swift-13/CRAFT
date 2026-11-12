int unknown();
/*@ requires x >= y  && y >= 0; */
void foo80(int x, int y,int z1,int z2,int z3) {

    int i = 0;
    
    
    while (unknown()) {
      if ( i < y )
      {
      i  = (i + 1);
      }

    }

    /*@ assert (i < y) ==> (i < x); */
    
  

}