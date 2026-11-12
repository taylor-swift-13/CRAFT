/*@ requires mid > 0; */
void foo145(int mid) {

    int lo;
    int hi;

    lo = 0;
    hi = 2 * mid;


    while(mid > 0){
       lo = lo + 1;
       hi = hi - 1;
       mid = mid - 1;
      }

    /*@ assert lo == hi; */

  }