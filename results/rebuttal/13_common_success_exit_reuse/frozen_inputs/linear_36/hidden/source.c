int unknown();

void foo36() {
    int c = 0;
  
    while (unknown()) {
      if (unknown()) {
        if (c != 40) {
          c  = c + 1;
        }
      } else {
        if (c == 40) {
          c  = 1;
        }
      }
    }

      /*@ assert (c != 40) ==> (c <= 40);*/
    
  
  }
  
