int unknown();
void foo115() {

    int sn = 0;
    int x = 0;
    
    
    while (unknown()) {
  
      x  = (x + 1);
      sn  = (sn + 1);
      
    }
       /*@ assert (sn != -1) ==> (sn == x);*/
      
    
  
  }