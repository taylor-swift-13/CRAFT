void foo29(int n) {

    int x = n;
    
    
    while (x > 0) {
      
       x  = x - 1;
  
    }
  
   /*@ assert (n >= 0) ==> (x == 0); */
  }