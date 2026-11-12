/*@ requires k > 0 && k < 20000001 && i == j; */
void foo176(int i, int j, int k) {

    int n;
    int b;

    n = 0;
    b = 1;


    while(n < (2 * k)){
       n = n + 1;
       if(b == 1){
       b = 0;
       i = i + 1;
      }
       else{
       b = 1;
       j = j + 1;
      }
      }

    /*@ assert (n >= (2 * k) ==> (i == j)); */

  }