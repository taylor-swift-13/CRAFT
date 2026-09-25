                                                                                  

void main(void) {
   int x, y;

   x = 0;
   y = 50;

  while( x < 100 ) {
    if( x < 50 ) {
      x = x+1;
    } else {
      x = x+1;
      y = y+1;
    }
  }

  if( y > 100 || y < 100 )
    goto __craft_label_0;

return;

{ __craft_label_0: {; 

}
}

}