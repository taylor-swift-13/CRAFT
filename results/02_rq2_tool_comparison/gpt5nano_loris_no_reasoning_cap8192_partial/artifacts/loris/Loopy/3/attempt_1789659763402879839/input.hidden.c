                                                                                     
extern int unknown_int(void);

;

/*@
  requires n>= 0;
*/
void main(int n){

   int i, a, b;
   

   i = 0; 
   a = 0; 
   b = 0;

   while( i < n ){
      if(unknown_int()) {
         a = a+1;
         b = b+2;
      } else {
         a = a+2;
         b = b+1;
      }
      i = i+1;
   }

   if ( a+b != 3*n)
      goto __craft_label_0;

return;

{ __craft_label_0: {; 

}
}
}