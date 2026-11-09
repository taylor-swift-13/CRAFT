// Source: data/benchmarks/LinearArbitrary-SeaHorn/VeriMAP/MAP-CPA-example_VeriMAP_true.c

void loopy_1(void) {
  int i = 0;
  int a = 0;

  while (1) {
    if (i == 20) {
       goto LOOPEND;
    } else {
       i++;
       a++;
    }

    if (i != a) {
      goto __loopgym_label_0;
    }
  }

  LOOPEND:

  if (a != 20) {
     goto __loopgym_label_0;
  }

  return;
  { __loopgym_label_0: {; 

}
}
  return;
}
