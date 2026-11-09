/*@ requires y > 0 || x > 0; */
void foo275(int x, int y) {



    while (x + y <= -2) {
       if (x > 0) {
       x++;
      }
       else {
       y++;
      }
      }

    

  }