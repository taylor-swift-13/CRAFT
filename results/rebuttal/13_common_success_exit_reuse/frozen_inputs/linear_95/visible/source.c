void foo95(int x) {
    int i =0;
    int j =0;
    int y =1;
    
    
    while (i <= x) {
      
      i  = i + 1;
      j  = j + y;
  
    }
  
  /*@ assert (y == 1) ==> (i == j) ;*/
  
  }