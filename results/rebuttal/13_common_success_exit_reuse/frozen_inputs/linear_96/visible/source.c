void foo96(int x) {
    int i =0;
    int j =0;
    int y =1;
    
    
    while (i <= x) {
      
      i  = i + 1;
      j  = j + y;
  
    }
  
  /*@ assert (i != j) ==> (y != 1) ;*/
  
  }