                                                                                
extern int unknown(void);

extern int unknown();

/*@
  requires bufferlen >1;
  requires inlen > 0;
  requires bufferlen < inlen;
*/
void main(int inlen, int bufferlen)
{
  
  int in;
  
  
  int buf;
  int buflim;

  
  
  

  buf = 0;
  in = 0;
  buflim = bufferlen - 2;
    
  while (unknown())
  {
    if (buf == buflim)
      break;
    {;

}

    {;

}
 
    buf++;
out:
    in++;
    {;

}

    {;

}

  }

    {;

}

    {;

}

  buf++;

  {;

}

  {;

}

  buf++;

 END:  return;
}