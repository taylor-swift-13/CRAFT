int unknown();
/*@ requires x >= 0 && x <= 2 && y <= 2 && y >= 0; */
void foo14(int x,int y,int z1,int z2,int z3) {

  
    while (unknown()) {
      
      x  = x + 2;
      y  = y + 2;

    }
      /*@ assert (y == 0) ==> (x != 4);  */
   
  }