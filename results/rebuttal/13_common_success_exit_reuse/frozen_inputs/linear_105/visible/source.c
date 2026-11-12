void foo105(int n,int v1,int v2,int v3) {
  
    int x = 0;
    
    
    while (x < n) {
      
      x  = (x + 1);
      
    }
    /*@ assert (n >= 0) ==> (x == n); */
    
  }