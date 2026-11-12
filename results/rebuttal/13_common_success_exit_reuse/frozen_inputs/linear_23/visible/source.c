void foo23() {
  
    int i=1;
    int j=20;
   
    
    while (j >= i) {
      
      i  = i + 2;
      j  = j - 1;
      
  
    }
  
    /*@ assert j == 13;*/
  
  }