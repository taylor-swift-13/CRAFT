int unknown();
/*@ requires n > 0; */
void foo180(int n) {

    int x;
    int m;

    x = 0;
    m = 0;


    while(x < n){
       if(unknown()){
       m = x;
      }
       x = x + 1;
      }

    /*@ assert (x >= n && n > 0) ==> (m < n); */

  }