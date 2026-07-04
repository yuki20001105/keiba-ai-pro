# Model Artifacts

This directory contains local and generated model artifacts used by the application.

## Rules

- Keep runtime-required models here when the app expects them to be present.
- Do not commit large experimental or one-off artifacts unless they are intentionally part of the release.
- Prefer a model registry, release artifact, or external storage for shared or reproducible model distribution.
- Generated caches, notebooks outputs, and temporary training files should stay out of Git.

## Current tracked models

The existing `.joblib` files are currently referenced by the application runtime and are kept for compatibility.
If a model becomes reproducible from training or can be loaded from another artifact source, move it out of Git in a later cleanup phase.
