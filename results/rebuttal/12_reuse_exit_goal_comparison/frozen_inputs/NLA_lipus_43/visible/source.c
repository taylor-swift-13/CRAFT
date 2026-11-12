/*@ requires a % 2 == 0 && a > 0;*/
int main43(int a){
  
  int r = 0;
  int x = a / 2;

  while (x > r){
        x = x - r;
        r = r + 1;
    }
  /*@ assert (r+1) * (r+1) >= a;*/
}

