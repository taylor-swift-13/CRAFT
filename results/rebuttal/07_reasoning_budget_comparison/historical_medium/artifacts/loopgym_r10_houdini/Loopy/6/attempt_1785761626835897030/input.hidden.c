// Source: data/benchmarks/LinearArbitrary-SeaHorn/VeriMAP/TRACER-paper-prog_d-pepm-proc.c_VeriMAP_true.c
extern unsigned int unknown_uint(void);

;

void errorFn() {__loopgym_label_0: goto __loopgym_label_0;}
/*@
  requires y>=0;
*/
void loopy_6(int y){

int x=0;

    

	while ( x < 10000) {
		y = y + 1;
		x = x + 1;
	}

	if( y + x < 10000)		
		goto __loopgym_label_0;

	return;
{ __loopgym_label_0: {; 

}
}
	return;
}