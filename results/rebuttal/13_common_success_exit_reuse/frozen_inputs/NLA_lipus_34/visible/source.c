/*@ requires a >= 1 && b >= 1;*/
int main34(int a,int b){

  int x=a;
  int y=b;
  int u=b;
  int v=0;

  while(x>y) {
    x=x-y;
    v=v+u;
  }
  /*@ assert x*u + y*v == a*b;*/
}

