int unknown();
/*@ requires x >= 0 && x <= 10 && y <= 10 && y >= 0; */
void foo11(int x, int y,int z1,int z2,int z3) {
  
  
    while (unknown()) {
      
        x = x + 10;
        y = y + 10;
    }

       /*@ assert (x == 20) ==>  (y != 0); */
    
  
   
  }