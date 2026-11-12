/*@ requires a <= 1000000 && b >= 0 && b <= 1000000; */
void foo191(int a, int b) {

    int res;
    int cnt;

    res = a;
    cnt = b;


    while(cnt > 0){
       cnt = cnt - 1;
       res = res + 1;
      }

    /*@ assert res == a + b; */

  }