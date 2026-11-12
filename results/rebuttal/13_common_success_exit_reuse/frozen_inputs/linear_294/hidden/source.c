/*@ requires xa + ya > 0; */
void foo294(int xa, int ya) {



    while (xa > 0) {
       xa--;
       ya++;
      }

    /*@ assert ya >= 0; */

  }