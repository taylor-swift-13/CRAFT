int unknown();
/*@ requires n > 0 && n < 10; */
void foo171(int n) {

    int i;
    int j;
    int k;
    int v;

    k = 0;
    i = 0;


    while(i < n){
       if(unknown()){
       i = i + 1;
       k = k + 4000;
       v = 0;
      }
       else{
       i = i + 1;
       k = k + 2000;
       v = 1;
      }

      }

    /*@ assert (i >= n) ==> (k > n); */

  }