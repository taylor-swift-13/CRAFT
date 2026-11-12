/*@ requires n > 0;*/
int main39(int n){

  int sum =0;
  int i=1;

  while (i <= n) {
        sum = sum + i;  
        i = i + 1;      
    }
  /*@ assert sum == (n * (n + 1)) / 2;*/
}

