// Source: data/benchmarks/accelerating_invariant_generation/invgen/sendmail-close-angle.c

/*@
  requires bufferlen >1;
  requires inlen > 0;
  requires bufferlen < inlen;
*/
void loopy_197(int __BLAST_NONDET, int inlen, int bufferlen)
{
  
  
  int in;
  
  
  int buf;
  int buflim;

  
  
  

  buf = 0;
  in = 0;
  buflim = bufferlen - 2;
    
  while (__BLAST_NONDET)
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