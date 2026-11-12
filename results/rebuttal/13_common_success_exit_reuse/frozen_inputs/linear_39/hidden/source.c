int unknown();
/*@ requires n > 0; */
void foo39(int n) {

    int c = 0;

    while (unknown()){
        
        if(c == n) {
            c = 1;
        }
        else {
            c = c + 1;
        }
    }
           
    /*@ assert  (c == n) ==> (c <= n); */


}