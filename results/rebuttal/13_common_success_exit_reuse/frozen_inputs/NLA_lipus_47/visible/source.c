/*@ requires x >= 0 && y >= 1;*/
int main47(int x,int y){
  
  int q = 0;
  int a = 0;
  int b = x;

  while(b != 0) {
        if (a + 1 == y) {
            q = q + 1;
            a = 0;
            b = b - 1;
        }
        else {
            a = a + 1;
            b = b - 1;
        }
    }
  /*@ assert q == x / y;*/
}

