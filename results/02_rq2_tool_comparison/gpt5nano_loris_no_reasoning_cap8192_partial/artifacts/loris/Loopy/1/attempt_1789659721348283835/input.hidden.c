                                                                                         

void main(void) {
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
      goto __craft_label_0;
    }
  }

  LOOPEND:

  if (a != 20) {
     goto __craft_label_0;
  }

  return;
  { __craft_label_0: {; 

}
}
  return;
}
