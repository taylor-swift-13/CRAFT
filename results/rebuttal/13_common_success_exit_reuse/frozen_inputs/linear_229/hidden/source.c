int unknown();
/*@ requires x == 1 || x == 2; */
void foo229(int x) {



    while (unknown()) {
       if(x == 1)
       x = 2;
       else if(x == 2)
       x = 1;
      }

    /*@ assert x <= 8; */

  }