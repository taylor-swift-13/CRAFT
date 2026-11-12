void foo30() {
    int x = 100;
    
    
    while (x > 0) {
      
      x  = x - 1;
      
    }
  
    /*@ assert x == 0; */
  }