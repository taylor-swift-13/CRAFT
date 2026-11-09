int unknown();
/*@
requires x >= 0;
*/
int main25(int x,int w){
	
	int z = w * x;

	while(unknown())
	{
		w = w * x;
		z = z * x;
	}

	
	return 0;
}