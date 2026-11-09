/*@ requires n > 0;*/
int main38(int n){

  
  int guess = n / 2;
  int prev_guess = 0;

  while (guess != prev_guess) {
        prev_guess = guess;
        guess = (guess + n / guess) / 2;  
    }
  
}

