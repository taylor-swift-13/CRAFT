void foo127(int x, int y,int z1,int z2,int z3) {

    int i = x;
    int j = y;
    
    
    while (x != 0) {
  
      x  = x - 1;
      y  = y - 1;
  
    }
  
    /*@ assert (y != 0) ==> (i != j); */
  
  }