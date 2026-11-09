// Source: data/benchmarks/LinearArbitrary-SeaHorn/invgen/sendmail-close-angle.c
extern int unknown(void);

extern int unknown();

/*@
  requires bufferlen >1;
  requires inlen > 0;
  requires bufferlen < inlen;
*/
void loopy_36(int inlen, int bufferlen)
{
  
  int in;
  
  
  int buf;
  int buflim;

  
  
  

  buf = 0;
  in = 0;
  buflim = bufferlen - 2;
    
  /*@
      loop invariant 0 <= in;
      loop invariant 0 <= buf;
      loop invariant buf <= buflim;
      loop invariant buf <= bufferlen - 2;
      loop invariant 0 <= in - buf;
      loop invariant buf - in == - (in - buf);
      loop invariant in <= in + (bufferlen - 2);
      loop invariant buf == 0 ==> in == 0;
      loop invariant 0 <= buflim;
      loop invariant 0 <= buf - in;
      loop invariant buflim == bufferlen - 2;
      loop invariant (in <= inlen) ==> (buf <= buflim);
      loop invariant buf <= in;
      loop invariant 0 <= in - in;
      loop invariant buf + 2 <= bufferlen;
      loop invariant (buf + 1) <= buflim + 1;
      loop invariant (in) == (in);
      loop invariant in - buf <= inlen;
      loop invariant in <= buflim;
      loop invariant buflim <= bufferlen - 2;
      loop invariant buf == 0 || buf >= 1;
      loop invariant 0 <= buf && buf <= buflim;
      loop invariant buf <= buflim ==> 0 <= buf;
      loop invariant buf == buflim ==> in == in;
      loop invariant (buf + 2 <= inlen) ==> (in >= 0);
      loop invariant (buf <= buflim) ==> (in - 0 >= 0);
      loop invariant (buf < buflim) ==> (in - in >= 0);
      loop invariant buf <= buflim + 2;
      loop invariant buf <= buflim + 1;
      loop invariant in <= buflim + 2;
      loop invariant 0 <= buflim - buf;
      loop invariant buf == in - 0;
      loop invariant buf == 0 || buf <= buflim;
      loop invariant in == 0 ==> buf >= 0;
      loop invariant 0 <= buf + 2;
      loop invariant (buf < buflim) ==> (buf + 1 <= buflim);
    */
    while (unknown())
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