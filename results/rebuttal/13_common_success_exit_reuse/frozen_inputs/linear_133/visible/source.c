/*@ requires n >= 0; */
void foo133(int n) {

    int x = 0;
  
    
    while (x < n) {
      {
       x  = x + 1;
      }
  
    }
    /*@ assert x == n; */
  
  }