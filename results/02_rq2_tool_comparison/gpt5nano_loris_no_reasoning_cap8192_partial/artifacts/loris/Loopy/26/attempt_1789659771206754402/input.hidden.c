                                                                                
extern int unknown(void);

extern int unknown();

/*@
  requires leader_len >0;
  requires bufsize >0;
  requires ielen >0;
  requires !(bufsize < leader_len);
  requires !(bufsize < 2*ielen);
*/
void main(int i, int leader_len, int bufsize, int ielen)
{
  
  int p;
  
  
  
  int bufsize_0 = unknown();
  

  
  
  

  

  p = 0;
  
  bufsize_0 = bufsize;
  bufsize -= leader_len;
  p += leader_len;

  

  {
  i = 0;
  while (i < ielen && bufsize > 2) {
    {;
    
    }
    
        {;
    
    }
    
        p += 2;
    i++;
  }
}

 END:;
}
