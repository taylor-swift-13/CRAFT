void foo24() {
  
    int i=1;
    int j=10;
   
    
    while (j >= i) {
      
      i  = i + 2;
      j  = j - 1;
      
  
    }
  
    /*@ assert j == 6;*/
  
  }