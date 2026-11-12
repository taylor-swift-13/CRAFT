int unknown();
void foo116(int v1,int v2,int v3) {

    int sn = 0;
    int x = 0;
    
    
    while (unknown()) {
  
      x  = (x + 1);
      sn  = (sn + 1);
      
    }
       /*@ assert (sn != x) ==> (sn == -1);*/
      
    
  
  }