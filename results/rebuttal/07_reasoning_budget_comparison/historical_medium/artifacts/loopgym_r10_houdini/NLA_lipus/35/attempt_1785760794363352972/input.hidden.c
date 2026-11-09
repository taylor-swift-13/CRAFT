/*@ requires a >= 1 && b >= 1;*/
int main35(int a,int b){
  
  int x=a;
  int y=b;
  int u=b;
  int v=0;

  while(x!=y) {
    if (x>y){
        x=x-y;
        v=v+u;
    }
    else {
        y=y-x;
        u=u+v;
    }
  }
  
}

