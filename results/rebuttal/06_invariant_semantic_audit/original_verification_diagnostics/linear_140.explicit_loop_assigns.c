/*@ requires v3 >= 0 && v1 <= 5 && (v1 - v3) >= 0 && v2 * 2 - v1 - v3 == 0 && v2 + 5 >= 0 && v2 <= 5; */
void foo140(int v1, int v2, int v3) {

    int x1;
    int x2;
    int x3;
    int t;

    x1 = 100;
    x2 = 75;
    x3 = -50;
    t = 0;


    /*@
      loop assigns t, v2, x1, x2, x3;
loop invariant x1 - 100 == v1 * t;
      loop invariant x3 + 50 == v3 * t;
      loop invariant t >= 0;
      loop invariant x1 - 100 + x3 + 50 == (v1 + v3) * t;
      loop invariant (x2 * 2 - x1 - x3) >= 0 ==> t >= 0;
      loop invariant (x2 * 2 - x1 - x3) <= 0 ==> t >= 0;
      loop invariant x1 == 100 + v1 * t;
      loop invariant x3 == -50 + v3 * t;
      loop invariant -100 <= x1;
      loop invariant v2 + 5 >= 0 ==> t >= 0;
      loop invariant (2 * x2 - x1 - x3) >= 0 ==> v2 == v2;
      loop invariant (2 * x2 - x1 - x3) <= 0 ==> v2 == v2;
      loop invariant v2 >= v2;
      loop invariant x1 == 100 + t*v1 - (t - 0) * 0;
      loop invariant (x2 * 2 - x1 - x3) >= 0 ==> v2 <= v2 + 1;
      loop invariant (x2 * 2 - x1 - x3) <= 0 ==> v2 >= v2 - 1;
      loop invariant (x2 * 2 - x1 - x3) >= 0 ==> v2 <= 5;
      loop invariant x1 - x3 == 150 + t * (v1 - v3);
      loop invariant (v1 - v3) == (v1 - v3);
      loop invariant v1 == v1;
      loop invariant v3 == v3;
      loop invariant (0 <= v1) ==> (t >= 0);
      loop invariant (v2 >= -5) ==> (t >= 0);
      loop invariant (v2 <= 5) ==> (t >= 0);
      loop invariant 0 <= v1 - 0;
    */
    while(v2 + 5 >= 0 && v2 <= 5){
       x1 = x1 + v1;
       x3 = x3 + v3;
       x2 = x2 + v2;
       if(x2 * 2 - x1 - x3 >= 0){
       v2 = v2 - 1;

      }
       else if(x2 * 2 - x1 - x3 <= 0){
       v2 = v2 + 1;
      }
       t = t + 1;
      }

    /*@ assert v3 >= 0; */

  }