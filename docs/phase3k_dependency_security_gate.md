# Phase 3K Dependency Security Gate

## Status

Phase 3K removes all npm **Critical** and **High** findings that can block a production release, aligns every checked JavaScript runtime with Node 24, and adds a release-blocking audit artifact. This is security hardening at L2; it does not provide staging evidence and does not make the application Production READY.

## Baseline

The pre-change lockfile reported 21 findings:

- Critical: 3
- High: 8
- Moderate: 8
- Low: 2

The critical/high paths included `concurrently -> shell-quote`, `@google-cloud/vision -> google-gax -> protobufjs/@grpc/grpc-js`, `next`, `form-data`, `lodash`, `picomatch`, `undici`, `vite`, and `ws`.

## Remediation boundary

- `next`, `concurrently`, and `postcss` move only within their existing major versions.
- Safe transitive releases are selected through the lockfile without `npm audit fix`, `--force`, or package overrides.
- `picomatch@4` is explicit so the `tinyglobby -> fdir` optional peer resolves to v4 while Tailwind's v2 consumers remain nested on fixed `2.3.2`.
- `package.json`, GitHub Actions, and all Next.js container stages use Node 24.
- No application route, authorization policy, database migration, worker, or deployment behavior is changed.

## Release-blocking evidence

The CI dependency-security job must:

1. perform a clean install with lifecycle scripts and platform-specific optional packages disabled;
2. prove `npm ls --all --omit=optional` has no invalid mandatory dependency edge;
3. produce full-graph and production-only `npm audit` JSON reports;
4. fail closed when either report is missing/malformed or contains any Critical/High count;
5. prove the executing runtime and `engines.node` are both Node 24;
6. upload both reports as `dependency-security-reports`, including on failure.

Optional packages remain represented in the lockfile and covered by the full
audit report. Omitting them from the installed-tree check prevents
platform-specific optional bindings from creating false failures between
Windows development and Linux CI.

## Residual findings

Moderate/Low advisories may remain when the current compatible dependency graph has no non-breaking remediation. They remain visible in the JSON evidence and must be reassessed in subsequent maintenance. They cannot be hidden by allowlists, audit suppression, or a forced downgrade.

## Readiness boundary

- Phase 3K result: dependency Critical/High gate ready.
- Production readiness: **NOT_READY**.
- L3 eligibility: **false** until controlled staging topology, approved external migrations, multi-instance recovery, and downstream-effect evidence are completed.
