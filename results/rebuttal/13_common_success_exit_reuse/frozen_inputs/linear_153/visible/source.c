int unknown();
void foo153() {

    int x;
    int y;
    int w;
    int z;

    w = 1;
    z = 0;
    x = 0;
    y = 0;


    while(unknown()){
       if(w == 1 && z == 0){
       x = x + 1;
       w = 0;
       y = y + 1;
       z = 1;
      }
       else if(w != 1 && z == 0){
       y = y + 1;
       z = 1;
      }
       else if(w == 1 && z != 0){
       x = x + 1;
       w = 0;
      }
       else if(w != 1 && z != 0){
       continue;
      }
      }

    /*@ assert x == y; */

  }