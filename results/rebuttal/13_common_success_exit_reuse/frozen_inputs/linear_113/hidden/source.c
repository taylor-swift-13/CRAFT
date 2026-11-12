/*@ requires n >= 1;
*/
void foo113(int n,int v1,int v2,int v3) {

    int i = 1;
    int sn = 0;
    
    
    while (i <= n) {
      
      i  = (i + 1);
      sn  = (sn + 1);
  
    }
    
    /*@ assert (sn!= 0) ==> (sn ==n); */
  
  }