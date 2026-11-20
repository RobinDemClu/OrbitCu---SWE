# OrbitCu report (LaTeX)

`orbitcu_report.tex` is the project document: Part I is the Phase 2 proposal
and requirements, Part II is the technical report on the work done so far
(registry, data sources, ingestion, prototype, difficulties). Figures are in
`figures/`; the compiled version is `orbitcu_report.pdf`.

Compile (TeX Live or MiKTeX, run twice for the table of contents):

```bash
cd docs/latex
pdflatex orbitcu_report.tex
pdflatex orbitcu_report.tex
```

Overleaf: upload the `.tex` file and the `figures/` folder.
