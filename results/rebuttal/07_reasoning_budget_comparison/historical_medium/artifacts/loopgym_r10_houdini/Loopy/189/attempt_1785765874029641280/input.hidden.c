// Source: data/benchmarks/accelerating_invariant_generation/invgen/NetBSD_g_Ctoc.c
int __BLAST_NONDET;
/*@
  requires BASE_SZ > 0;
*/
void loopy_189(int BASE_SZ)
{
  
  int i;
  int j;
  int len = BASE_SZ;

  

  {;

}

   
  
  i = 0;
  j = 0;
  while (1) {
    if ( len == 0 ){ 
      goto END;
    } else {
      {;

}
 {;

}

      {;

}
 {;

}

      if ( __BLAST_NONDET ) {
        i++;
        j++;
        goto END;
      }
    }
    i ++;
    j ++;
    len --;
  }

 END:  return;
}
