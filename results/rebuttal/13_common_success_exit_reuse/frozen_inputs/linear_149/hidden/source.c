int unknown();
void foo149() {

    int x;
    int y;
    int i;
    int j;

    x = 0;
    y = 0;
    j = 0;
    i = 0;


    while(unknown()){
       x = x + 1;
       y = y + 1;
       i = x + i;
       if(unknown()){
       j = y + j;
      }
       else{
       j = y + j + 1;
      }
      }

    /*@ assert j >= i; */

  }
