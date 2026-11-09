/*@ requires a>=n && n==0;*/
int main1(int a,int n){
 
  int x,y,z;
  
  x=0;
  y=1;
  z=6;
  

  while(n<=a){
       n=n+1;
       x=x+y;
       y=y+z;
       z=z+6;
  }
  
}
