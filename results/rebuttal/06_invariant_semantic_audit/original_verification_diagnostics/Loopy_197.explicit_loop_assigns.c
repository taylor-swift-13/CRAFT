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
    
  /*@
      loop assigns buf, in;
loop invariant 0 <= buf;
      loop invariant 0 <= in;
      loop invariant buf <= buflim;
      loop invariant buf <= bufferlen - 2;
      loop invariant buflim == bufferlen - 2;
      loop invariant in == buf * 0 + in;
      loop invariant buf < bufferlen;
      loop invariant (buf + 2) <= bufferlen;
      loop invariant (in == buf) ==> (buf <= buflim);
      loop invariant buf <= in;
      loop invariant buflim >= 0;
      loop invariant __BLAST_NONDET == __BLAST_NONDET;
      loop invariant 0 <= buf && buf <= buflim;
      loop invariant in <= in + (bufferlen - 2 - buflim);
      loop invariant in == 0 ==> buf == 0;
      loop invariant 0 <= bufferlen - 2;
      loop invariant 0 <= bufferlen - 2 - buf;
      loop invariant buf - 0 <= buf;
      loop invariant 0 <= buflim - buf;
      loop invariant buf == in - 0;
      loop invariant in <= buflim + 2;
      loop invariant buf <= buflim + 1;
      loop invariant __BLAST_NONDET >= 0 ==> (buf <= buflim);
      loop invariant (buf == buflim) ==> (in >= 0);
      loop invariant in <= bufferlen - 1;
      loop invariant buf + 1 <= buflim + 1;
      loop invariant (buf + 1) - buf == 1;
      loop invariant in - buf == 0;
      loop invariant (buf >= 0) && (in >= 0);
      loop invariant buflim <= bufferlen - 2;
      loop invariant buf < buflim ==> __BLAST_NONDET == __BLAST_NONDET;
      loop invariant (buf < buflim) ==> (buf <= buflim);
      loop invariant buf + in <= buflim + in;
      loop invariant buf <= bufferlen - 1;
      loop invariant buf < buflim ==> in <= in + 0;
      loop invariant buf == 0 || buf == 1 || buf == 2 || buf == 3 || buf <= buflim;
      loop invariant in <= in + 1;
      loop invariant buf == buflim || (buf < buflim ==> in == in);
    */
    while (__BLAST_NONDET)
  {
    if (buf == buflim)
      break;
    {;
//@ assert(0<=buf);
}

    {;
//@ assert(buf<bufferlen);
}
 
    buf++;
out:
    in++;
    {;
//@ assert(0<=in);
}

    {;
//@ assert(in<inlen);
}

  }

    {;
//@ assert(0<=buf);
}

    {;
//@ assert(buf<bufferlen);
}

  buf++;

  {;
//@ assert(0<=buf);
}

  {;
//@ assert(buf<bufferlen);
}

  buf++;

 END:  return;
}