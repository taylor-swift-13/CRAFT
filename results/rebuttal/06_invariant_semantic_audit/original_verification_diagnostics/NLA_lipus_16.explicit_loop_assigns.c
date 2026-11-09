/*@
requires k >= 0 && k <= 30;
*/
int main16(int k){
   
    int y=0;
    int x=0;
    int c=0;


    // loop body
    /*@
      loop assigns c, x, y;
loop invariant c >= 0;
      loop invariant c <= k;
      loop invariant y == c;
      loop invariant y >= 0;
      loop invariant x >= 0;
      loop invariant y <= k;
      loop invariant x <= k*(k*k);
      loop invariant (c == k) ==> (y == k);
      loop invariant (c == k) ==> (x == k*(k+1)*(2*k+1)/6);
      loop invariant c <= k && y <= k && x <= k*(k+1)*(2*k+1)/6;
      loop invariant y <= c;
      loop invariant c >= 0 ==> y >= 0;
      loop invariant c >= 0 ==> x >= 0;
      loop invariant x == y * y + x - y * y;
      loop invariant 0 <= c <= k;
      loop invariant c == y && y >= 0;
      loop invariant x == 0 ==> y == 0;
      loop invariant x == x;
      loop invariant 0 <= (c % 1);
      loop invariant x <= k*(k+1)*(2*k+1)/3;
      loop invariant x == c * (c + 1) * (2 * c + 1) / 6;
      loop invariant c <= k ==> y <= k;
    */
    while(c < k){
     c = c + 1;
     y = y + 1;
     x = y * y + x;
    }
    /*@ assert 6*x-2*k*k*k-3*k*k-k == 0; */
}
