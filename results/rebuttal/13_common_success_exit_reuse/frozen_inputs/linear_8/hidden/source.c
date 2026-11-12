int unknown();
/*@ requires x >= 0 && x <= 10 && y <= 10 && y >= 0; */
void foo8(int x, int y) {
  
  
    while (unknown()) {
      
        x = x + 10;
        y = y + 10;
        
      
    }

    /*@ assert (y == 0) ==>  (x != 20); */
    
  }