/*@ requires n <= 20000001; */
void foo300(int n) {

    int i;
    int k;
    int j;

    i = 0;
    j = 0;
    k = 0;


    while(i < n){
       i = i + 3;
       if((i % 2) != 0)
       j = j + 3;
       else
       k = k + 3;
      }

    /*@ assert (n > 0) ==> (i / 2 <= j); */

  }