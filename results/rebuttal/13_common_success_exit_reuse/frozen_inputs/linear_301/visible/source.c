int unknown();
/*@ requires n <= 50000001; */
void foo301(int n) {

    int i;
    int j;

    i = 0;
    j = 0;


    while(i < n){
       if(unknown())
       i = i + 6;
       else
       i = i + 3;
      }

    /*@ assert i % 3 == 0; */

  }