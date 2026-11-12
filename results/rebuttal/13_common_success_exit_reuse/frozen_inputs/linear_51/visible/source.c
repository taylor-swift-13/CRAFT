int unknown();

void foo51(int n) {

    int c = 0;

    while (unknown()) {
        {
          if (unknown()) {
            if ( c != 4 )
            {
            c  = c + 1;
            }
          } else {
            if (c == 4) 
            {
            c  = 1;
            }
          }
    
        }
    
    } 
          
    /*@ assert  (c != 4 ) ==> (c <= 4); */
  

}