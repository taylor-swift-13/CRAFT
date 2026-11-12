/*@ requires x > 0; */
void foo154(int x) {

    int i;
    int j;

    j = 0;
    i = 0;


    while(i < x){
       j = j + 2;
       i = i + 1;
      }

    /*@ assert (i >= x) ==> (j == 2 * x); */

  }