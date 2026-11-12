void foo1() {
  
    int x = 1;
    int y = 0;
    
    
    while (y < 100000) {
      
       x  = x + y;
       y  = y + 1;
      
  
    }
  
    /*@ assert x >= y; */
  }