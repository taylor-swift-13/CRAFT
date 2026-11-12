int unknown();
void foo114() {

    int sn = 0;
    int x = 0;
    
    
    while (unknown()) {
  
      x  = (x + 1);
      sn  = (sn + 1);
      
    }
    /*@ assert (sn != x) ==> (sn == -1);*/
  
  }