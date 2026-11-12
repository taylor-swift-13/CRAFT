/*@ requires a % 2 == 0 && a > 0;*/
int main44(int a){

  
  int r = 0;
  int x = a / 2;

  while (x > r){
        x = x - r;
        r = r + 1;
    }
  /*@ assert a == 2*x + r*r - r;*/
}

