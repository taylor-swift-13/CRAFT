/*@
requires k>=0 && k<=30;
*/
int main18(int k){
   
    int y=0;
    int x=0;
    int c=0;

    // loop body
    /*@
      loop invariant c >= 0;
      loop invariant c <= k;
      loop invariant y >= 0;
      loop invariant x >= 0;
      loop invariant c == y;
      loop invariant y <= k;
      loop invariant 0 <= c <= k;
      loop invariant x == y * y * y * y + (x - (y * y * y * y));
      loop invariant 0 <= y <= k;
      loop invariant x == y*y*y*y + x - y*y*y*y;
      loop invariant 0 <= k;
      loop invariant y*y >= 0;
      loop invariant c <= k ==> y == c;
      loop invariant (c > 0 ==> y > 0);
      loop invariant x == (c * (c + 1) * (2 * c + 1) * (3 * c * c + 3 * c - 1)) / 30;
      loop invariant x >= y*y*y*y;
    */
    while(c < k){
     c = c +1 ;
     y = y +1;
     x = y*y*y*y+x;
    }

    /*@ assert 6*k*k*k*k*k + 15*k*k*k*k+ 10*k*k*k - 30*x - k == 0; */
}
