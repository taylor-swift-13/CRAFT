void foo125(int x, int y) {

    int i = x;
    int j = y;
    
    
    while (x != 0) {
  
      x  = x - 1;
      y  = y - 1;
  
    }
  
    /*@ assert (y != 0) ==> (i != j); */
  
  }