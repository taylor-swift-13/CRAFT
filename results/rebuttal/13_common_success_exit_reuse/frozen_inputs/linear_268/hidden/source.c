/*@ requires x >= 0 && x <= y && y < n; */
void foo268(int n, int x, int y) {



    while (x < n) {
       x = x + 1;
       if(x > y)
       y = y + 1;
      }

    /*@ assert y == n; */

  }