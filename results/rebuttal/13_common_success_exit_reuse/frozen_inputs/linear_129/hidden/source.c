void foo129(int y,int z1,int z2,int z3) {

    int x = 1;
    
    
    while (x < y) {
  
       x  = x + x;
       
    }
  
    /*@ assert x >= 1;*/
  
  }