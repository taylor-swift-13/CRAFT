void foo128(int y) {

    int x = 1;
    
    
    while (x < y) {
  
       x  = x + x ;
       
    }
  
    /*@ assert x >= 1;*/
  
  }