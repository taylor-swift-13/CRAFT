MAIN = main
LATEX = pdflatex -interaction=nonstopmode
BIBTEX = bibtex

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex sections/*.tex figures/*.pdf references.bib \
	iclr2027_conference.sty iclr2027_conference.bst
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
