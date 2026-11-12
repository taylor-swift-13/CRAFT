/*@ requires n > 0;*/
int main31(int n,int a){

  int sn = 0;
  int i = 0;

  while(i < n) {
    sn = sn + a;
    i++;
  }
  /*@ assert sn == n*a || sn == 0;*/
}

