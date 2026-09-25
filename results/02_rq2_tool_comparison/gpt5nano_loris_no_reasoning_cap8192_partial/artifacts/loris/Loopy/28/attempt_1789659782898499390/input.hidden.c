                                                                       

/*@
  requires MAXPATHLEN > 0;
*/
void main(int MAXPATHLEN, int glob2_p_off)
{
  
  
  int pathbuf_off = unknown ();

  int bound_off;

  
  int glob2_pathbuf_off;
  int glob2_pathlim_off;

  

  pathbuf_off = 0;
  bound_off = pathbuf_off + (MAXPATHLEN + 1) - 1;

  glob2_pathbuf_off = pathbuf_off;
  glob2_pathlim_off = bound_off;

  {
  glob2_p_off = glob2_pathbuf_off;
  while (glob2_p_off <= glob2_pathlim_off) {
    {;
    
    }
     {;
    
    }
    glob2_p_off++;
  }
}

 END:  return;
}