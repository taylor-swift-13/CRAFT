/*@ requires n >= 0 && m >= 0 && m < n; */
void foo165(int m, int n) {

    int x;
    int y;

    x = 0;
    y = m;


    while(x < n){
       if(x + 1 <= m){
       x = x + 1;
      }
       else if (x + 1 > m){
       x = x + 1;
       y = y + 1;
      }
      }

    /*@ assert (x >= n) ==> (y == n); */

  }