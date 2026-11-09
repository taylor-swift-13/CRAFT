int unknown();
void foo186() {

    int x;
    int y;
    int z;
    int w;

    x = 0;
    y = 0;
    z = 0;
    w = 0;


    while(unknown()){
       if(x >= 4){
       x = x + 1;
       y = y + 3;
       z = z + 10;
       w = w + 10;
      }
       else if(x >= z && w > y){
       x = 0 - x;
       y = 0 - y;
      }
       else{
       x = x + 1;
       y = y + 2;
      }
      }

    

  }