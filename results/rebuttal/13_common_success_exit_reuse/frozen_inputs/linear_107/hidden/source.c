
void foo107(int m,int j,int a) {

    int k = 0;
    
    
    while ( k < 1) {

        if(m < a) {
            m = a;
        }
        
        k = k + 1;
    }

  /*@ assert a <= m; */

}