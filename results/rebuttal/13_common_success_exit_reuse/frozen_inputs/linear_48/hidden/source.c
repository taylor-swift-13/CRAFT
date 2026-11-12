int unknown();

/*@ requires n > 0; */
void foo48(int n) {

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
             
    /*@ assert (c == n) ==> (n > - 1); */
    

  

}