
int unknown();
/*@ requires x >= y  && y >= 0; */
void foo79(int x, int y) {

    int i = 0;
    
    
    while (unknown()) {
      if ( i < y )
      {
      i  = (i + 1);
      }
  
    }
  
    /*@ assert (i < 0 && i >= x) ==> (i >= y); */
    
}