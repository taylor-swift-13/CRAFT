int unknown();
void foo155() {

    int j;
    int k;
    int t;

    j = 2;
    k = 0;


    while(unknown()){
       if (t == 0){
       j = j + 4;
      }
       else {
       j = j + 2;
       k = k + 1;
      }
      }

    /*@ assert (k != 0) ==> (t != 0 && j == k * 2 + 2); */

  }