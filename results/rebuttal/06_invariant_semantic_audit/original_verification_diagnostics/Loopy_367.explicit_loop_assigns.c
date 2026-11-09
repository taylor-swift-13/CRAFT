// Source: data/benchmarks/sv-benchmarks/loop-invgen/sendmail-close-angle.c
extern int unknown_int(void);

/*@
  requires bufferlen >1;
  requires inlen > 0;
  requires bufferlen < inlen;
*/
void loopy_367(int inlen, int bufferlen)
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
      loop invariant buf <= buflim + 1;
      loop invariant buf <= bufferlen - 2;
      loop invariant buf <= buflim;
      loop invariant 0 <= bufferlen - 2;
      loop invariant 0 <= buf && buf <= buflim;
      loop invariant buf - in <= buflim - 0;
      loop invariant buflim >= 0;
      loop invariant buf + 1 == in + 1 ==> buf == in;
      loop invariant buf == in + (buf - in);
      loop invariant in <= buflim + 2;
      loop invariant (buf + in) % 1 == 0;
      loop invariant 0 <= buf && 0 <= in;
      loop invariant buf == in;
      loop invariant buflim == bufferlen - 2;
      loop invariant buf <= in;
      loop invariant (buf < buflim) ==> (in <= buflim);
      loop invariant (buf == buflim) ==> (in <= buflim);
      loop invariant buflim <= bufferlen - 2;
      loop invariant 0 <= buf && buf <= buflim && 0 <= in;
      loop invariant buf <= in + buflim;
      loop invariant (buf <= buflim) ==> (in <= in + buflim);
      loop invariant (buf < buflim) ==> (in < in + buflim);
      loop invariant (buf == buflim) ==> (in <= in + buflim);
      loop invariant buf + 2 <= bufferlen;
      loop invariant bufferlen > 1;
      loop invariant inlen > 0;
      loop invariant 0 <= (buflim - buf);
      loop invariant (in == inlen) ==> (buf <= buflim);
      loop invariant (buf + 2 <= inlen) ==> (in <= inlen);
      loop invariant (in <= inlen) ==> (buf <= buflim);
      loop invariant 0 <= buf - (in - 2);
      loop invariant in <= bufferlen - 2 + inlen;
      loop invariant (buf < buflim) ==> (buf + 2 <= bufferlen);
      loop invariant (buf == buflim) ==> (buf + 2 <= bufferlen);
      loop invariant (buf < buflim) ==> (in >= 0);
      loop invariant (buf == buflim) ==> (in >= 0);
      loop invariant (buf == 0) ==> (in == 0);
      loop invariant (buf >= 0) ==> (in >= 0);
      loop invariant (buf <= buflim) ==> (in <= in);
      loop invariant 0 <= in - buf;
      loop invariant 0 <= buf + in;
      loop invariant buf + in == (buf + in);
      loop invariant in <= in + buflim;
      loop invariant 0 <= bufferlen - 2 - buf;
      loop invariant in <= in + 0;
    */
    while (unknown_int())
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