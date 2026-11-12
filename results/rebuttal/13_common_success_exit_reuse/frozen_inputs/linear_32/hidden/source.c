void foo32(int n, int v1,int v2,int v3) {

    int x = n;
    
    
    while (x > 1) {
      
       x  = x - 1;
  
    }
   /*@ assert (n >= 1) ==> (x == 1); */
  }