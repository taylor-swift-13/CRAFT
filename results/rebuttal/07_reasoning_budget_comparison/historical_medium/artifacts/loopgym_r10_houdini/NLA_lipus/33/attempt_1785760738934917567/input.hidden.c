/*@ requires A >= 1 && (R-1)*(R-1) < A && A <= R*R && A%2 ==1;*/
int main33(int A, int R){
  
  int u=2*R+1;
  int v=1;
  int r=R*R-A;

  while(r>0) {
    r=r-v;
    v=v+2;
  }
  
}

