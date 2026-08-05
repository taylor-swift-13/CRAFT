MAIN = main
LATEX = pdflatex
BIBTEX = bibtex

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex sections/*.tex figures/*.pdf references.bib \
	iclr2026_conference.sty iclr2026_conference.bst
	$(LATEX) $(MAIN)
	$(BIBTEX) $(MAIN)
	$(LATEX) $(MAIN)
	$(LATEX) $(MAIN)

clean:
	rm -f *.aux *.bbl *.blg *.log *.out *.toc *.fdb_latexmk *.fls *.synctex.gz
	rm -f sections/*.aux

distclean: clean
	rm -f $(MAIN).pdf

.PHONY: all clean distclean
