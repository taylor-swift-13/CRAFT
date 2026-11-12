int unknown();

/*@ requires n > 0; */
void foo47(int n) {

    int c = 0;

    while (unknown()) {
        {
          if (unknown()) {
            if ( c != n )
            {
            c  = c + 1;
            }
          } else {
            if (c == n) 
            {
            c  = 1;
            }
          }
    
        }
    
    }    
    /*@ assert ((c < 0)&& (c > n)) ==> (c == n); */
    

  

}