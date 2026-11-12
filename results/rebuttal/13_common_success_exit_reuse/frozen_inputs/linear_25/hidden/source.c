void foo25() {
    int x = 10000;
    
    
    while (x > 0) {
      
      x  = x - 1;
      
    }
  
    /*@ assert x == 0; */
  }