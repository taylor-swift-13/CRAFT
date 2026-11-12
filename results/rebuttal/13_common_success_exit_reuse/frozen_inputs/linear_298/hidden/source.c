void foo298() {

    int x;
    int z;

    x = 0;
    z = 5000000;


    while(x < 10000000){
       if(x >= 5000000)
       z--;
       x++;
      }

    /*@ assert z == 0; */

  }