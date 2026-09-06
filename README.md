# API contract end-to-end suite

Status: **contract-only**. This repository is the independent acceptance boundary for the ORES Chat public, customer, administrator, and internal HTTP contracts. It now executes peer-authority convergence and repository-owned Rust parity tests, but it does not yet claim deployed endpoint coverage.

## Authority boundary

The production repository owns two independent, human-authored lanes:

```text
TypeSpec A ──compiler/emitter──> comparison-only JSON Schema B
JSON Schema A ────────────────> independent normalized declaration model
```

This test repository owns neither lane. Its composite action snapshots TypeSpec A, JSON Schema A, and the independent instance corpus; runs the immutable `ORESoftware/typespec-json-schema-validator`; optionally runs production Rust parity slices; and then proves the authored bytes did not change. Generated JSON Schema B and receipts stay under an isolated evidence directory.

A production workflow should check out its own exact revision and pin this action by the exact merged commit:

```yaml
- uses: ores-chat-test/api-contract-e2e@<full-commit-sha>
  with:
    source-root: .
    run-rust-tests: "true"
```

No private cross-organization checkout token is required. The caller retains its normal repository permissions, and this action receives only the existing checkout.

## Evidence

The repository CI executes:

1. standard-library unit tests for path isolation, symlink rejection, Draft 2020-12 enforcement, Rust preconditions, mutation detection, and receipt generation;
2. a convergent TypeSpec/JSON Schema fixture through the real pinned compiler-backed validator;
3. a deliberately drifted required-field fixture that must fail closed;
4. retained JSON, SARIF, source-digest, and sibling-org receipt artifacts.

The machine-readable plan remains in `suite.json`. `contract-only` remains accurate until CI exercises real `ores-chat` and isolated `ores-chat-test` deployments with distinct credentials for every trust plane, correlation checks, and redacted hosted evidence.
