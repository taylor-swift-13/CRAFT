void foo98(int x) {
    int i =0;
    int j =0;
    int y =2;
    
    
    while (i <= x) {
      
      i  = i + 1;
      j  = j + y;
  
    }
  
  /*@ assert (i != j) ==> (y != 1) ;*/
  
  }