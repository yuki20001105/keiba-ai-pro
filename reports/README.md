# Reports directory

This directory separates immutable Git evidence from local runtime output.

- `evidence/`: reviewed historical evidence tracked by Git. Do not overwrite it
  from generators; create a new generated report and review it first.
- `generated/`: generated or temporary output. It is ignored by Git and may be
  removed after any required external archive has been verified.
- files created directly under `reports/` by legacy scripts are also ignored;
  new or updated generators should write under `generated/`.
- live-validation inputs: stored outside this tree at
  `keiba/data/live-validation-inputs/` with the local runtime database.

Some evidence JSON records its original `reports/<name>` path. Those embedded
paths are historical provenance and intentionally remain unchanged after moving
the files under `evidence/`.
