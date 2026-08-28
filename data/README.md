# data/

Datasets live here. **Everything except this README is gitignored.**

Recommended layout:

```
data/
├── raw/        # original, immutable data — never edit
├── interim/    # cleaned / intermediate artifacts
├── processed/  # model-ready data
└── external/   # third-party data with clear provenance
```

For every dataset, document in this README:

- Source (URL, paper, collaborator) and license
- Version and download date
- Preprocessing script that produced it (path in the repo)
- sha256 of archives, if applicable (`python -m harness hash <file>`)

Large datasets that don't fit in git belong in shared storage; record the path
and hash here so runs remain traceable.
