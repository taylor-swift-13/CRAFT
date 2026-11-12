/*@ requires nlen == idBitLength / 32 && idBitLength >= 0 && material_length >= 0; */
void foo246(int idBitLength, int material_length, int nlen) {

    int j;
    int k;

    j = 0;


    while((j < idBitLength / 8) && (j < material_length)){
       j = j + 1;
      }

    /*@ assert 0 <= j; */

  }