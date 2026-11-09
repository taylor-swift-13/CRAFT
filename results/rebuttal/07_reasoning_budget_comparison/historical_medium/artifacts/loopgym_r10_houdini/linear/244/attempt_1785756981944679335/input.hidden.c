int unknown();
/*@ requires (st == 0 && last < c) || (st == 1 && last >= c); */
void foo244(int c, int last, int st) {

    int a;
    int b;

    a = 0;
    b = 0;
    c = 200000;


    while(unknown()){
       if(st == 0 && c == last + 1){
       a = a + 3;
       b = b + 3;
      }
       else{
       a = a + 2;
       b = b + 2;
      }
       if(c == last && st == 0){
       a = a + 1;
       c = c + 1;
      }

      }

    

  }