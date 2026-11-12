int unknown();
/*@ requires x >= y  && y >= 0; */
void foo77(int x, int y) {

    int i = 0;
    
    
    while (unknown()) {
      if ( i < y )
      {
      i  = (i + 1);
      }
  
    }
    /*@ assert (i < y) ==> (i < x); */
    
  

}