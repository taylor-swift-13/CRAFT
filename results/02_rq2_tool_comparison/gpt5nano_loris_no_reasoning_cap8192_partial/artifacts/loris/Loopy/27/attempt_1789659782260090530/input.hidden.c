                                                                         
extern int unknown(void);

extern int unknown();

/*@
  requires BASE_SZ > 0;
*/
void main(int BASE_SZ, int __BLAST_NONDET)
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
