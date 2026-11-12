/*@ requires n >= 1;
*/
void foo111(int n) {

    int i = 1;
    int sn = 0;
    
    
    while (i <= n) {
      
      i  = (i + 1);
      sn  = (sn + 1);
  
    }
    
    /*@ assert (sn!= 0) ==> (sn ==n); */
  
  }