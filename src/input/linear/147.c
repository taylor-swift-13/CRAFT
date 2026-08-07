int unknown();
void foo147() {

    int x;
    int y;
    int xa;
    int ya;

    xa = 0;
    ya = 0;


    while(unknown()){
       x = xa + ya * 2 + 1;
       if(unknown()){
       y = ya - xa * 2 + x;
      }
       else{
       y = ya - xa * 2 - x;
      }
       xa = x - y * 2;
       ya = x * 2 + y;
      }

    /*@ assert xa + ya * 2 >= 0; */

  }
