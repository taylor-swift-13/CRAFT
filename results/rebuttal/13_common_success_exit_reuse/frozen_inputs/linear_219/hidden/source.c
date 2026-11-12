int unknown();
void foo219() {

    int s;

    s = 0;


    while(unknown()){
       if (s != 0){
       s = s + 1;
      }
      }

    /*@ assert s == 0; */

  }