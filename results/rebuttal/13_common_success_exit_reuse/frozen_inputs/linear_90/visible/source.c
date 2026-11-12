int unknown();

int foo90(int x,int v1,int v2,int v3) {

    int lock = 1;
    int y = x + 1;

    while (x != y) {
        if (unknown()) {
          
          lock  = 1;
          x  = y;
          
        } else {
          
          lock  = 0;
          x  = y;
          y  = y + 1;
          
        }
 
    }
    /*@ assert lock == 1;*/
  }