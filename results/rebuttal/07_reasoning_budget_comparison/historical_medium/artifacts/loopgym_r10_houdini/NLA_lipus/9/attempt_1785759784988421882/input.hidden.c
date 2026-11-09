/*@
requires z>=0 && z<=10 && k>0 && k<=10;
*/

int main9(int z,int k){
  

  int x=1;
  int y=z;
  int c=1;

  while (c < k){
 c = c + 1;
 x = x*z + 1;
 y = y*z;
  }


}
