void foo92(int z1,int z2,int z3) {

    int x = 0;
    int y = 0;

    
    while(y >= 0) {
        y = y + x;
    }
    /*@ assert y >= 0;*/

}