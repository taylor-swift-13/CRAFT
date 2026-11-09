/*@ requires X > 0 && Y > 0 && X >= Y;*/
int main41(int X, int Y){

    
  int v = 2 * Y - X;
  int y = 0;
  int x = 0;

  while (x <= X) {
        if (v < 0) {
            v = v + 2 * Y;
        } else {
            v = v + 2 * (Y - X);
            y++;
        }
        x++;

    }
  
}

