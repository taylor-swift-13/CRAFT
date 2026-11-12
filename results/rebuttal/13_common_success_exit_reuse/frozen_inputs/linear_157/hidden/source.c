int unknown();
/*@ requires m > 0; */
void foo157(int m) {

    int a;
    int j;

    a = 0;
    j = 1;


    while(j <= m){
       if(unknown()){
       a = a + 1;
      }
       else{
       a = a - 1;
      }
       j = j + 1;
      }

    /*@ assert (j > m) ==> (a >= 0 - m); */

  }