int unknown();
/*@ requires x > -100 && x < 200 && z > 100 && z < 200; */
void foo206(int x, int z) {



    while(x < 100 && z > 100){
       if(unknown()){
       x = x + 1;
      }
       else{
       x = x - 1;
       z = z - 1;
      }
      }

    

  }