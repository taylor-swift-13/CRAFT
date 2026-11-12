int unknown();
void foo302() {

    int i;
    int j;

    i = 0;


    while(i < 50000001){
       if(unknown())
       i = i + 8;
       else
       i = i + 4;
      }

    /*@ assert (j == (i / 4) ==> ((j * 4))); */

  }