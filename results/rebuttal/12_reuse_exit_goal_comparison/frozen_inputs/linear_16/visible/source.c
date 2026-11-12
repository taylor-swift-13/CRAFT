int unknown();
void foo16(int n) {
    int x = 0;
    int m = 0;

    
    while (x < n) {
        
        if (unknown()) {
            m = x;
        }
        
        x = x + 1;
    }

    /*@ assert (n > 0) ==> (m >= 0); */

}