/*@
requires x >= 0 && y >= x;
*/
int main27(int x,int y) {
	
	int z=0;
	int w=0;

	while(w < y) {
		z += x;
		w += 1;
	}

	/*@ assert z == x * y; */

	return 0;
}
