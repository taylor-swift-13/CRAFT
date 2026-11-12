/*@ requires a >= 0 && b >= 0;*/
int main42(int a,int b){


  int product =0;
  int i=0;
  
  while (i < b) {
        product = product + a;  
        i = i + 1;
    }
  /*@ assert product == a * b;*/
}

