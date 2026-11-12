void foo102(int n) {
  
    int x = 0;
    
    
    while (x < n) {
      
      x  = (x + 1);
      
    }
    /*@ assert (n >= 0) ==> (x == n); */
    
  }