                                                                                                             
extern int unknown_int(void);

void main(void){

	int x=0;
	int y=0;

	while (unknown_int()) {
		if (unknown_int()) {
			x = x+1; 
			y = y+2;
		} else if (unknown_int()) {
			if (x >= 4) {
			    x = x+1; 
			    y = y+3; 
			}
		} 
	}

    if(3*x < y)
		goto __craft_label_0;
	
	return;
{ __craft_label_0: {; 

}
}
	return;
}