# Schemas

DDL, ER diagram sources, and CSV header specifications.

Expected contents:

- One schema definition per domain.
- SQLFluff-clean DDL.
- dbdiagram.io source files and exported ER diagram PDFs.
- CSV header specs with byte-identical names to DDL columns.
- Join path declarations, including INNER and LEFT OUTER semantics.
- Documentation of where imperfections are injected.

Schemas must remain platform agnostic. Avoid Pulse-specific naming or structure.
