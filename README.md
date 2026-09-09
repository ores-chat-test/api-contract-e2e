# API contract end-to-end suite

Status: **contract-only**. This repository is the independent acceptance boundary for the ORES Chat public, customer, administrator, and internal HTTP contracts. It executes peer-authority convergence and repository-owned Rust parity tests, but it does not yet claim deployed endpoint coverage.

## Authority boundary

The production repository owns two independent, human-authored lanes:

```text
TypeSpec A ──compiler/emitter──> comparison-only JSON Schema B
JSON Schema A ────────────────> independent normalized declaration model
```

This test repository owns neither lane. Its composite action snapshots TypeSpec A, JSON Schema A, and the independent instance corpus; runs the immutable `ORESoftware/typespec-json-schema-validator`; optionally runs production Rust parity slices; and then proves the authored bytes did not change. Generated JSON Schema B and receipts stay under an isolated evidence directory.

The action also emits a parity-approved Contract IR at `<evidence-root>/contract-ir.json`. That IR is a **downstream-derived, non-editable artifact**, not a third source of truth. Consumers must verify it against the current TypeSpec, current independently authored JSON Schema, fresh generated comparison witness, exact parity receipt, and their complete expected declaration inventory before code generation, database projection, RPC binding, or runtime admission.

A production workflow should check out its own exact revision and pin this action by the exact merged commit:

```yaml
- id: sibling-contract
  uses: ores-chat-test/api-contract-e2e@<full-commit-sha>
  with:
    source-root: .
    run-rust-tests: "true"
```

The `receipt` and `contract_ir` outputs point to the retained evidence paths. No private cross-organization checkout token is required. The caller retains its normal repository permissions, and this action receives only the existing checkout.

The `int64-strategy` input defaults to lossless `string` encoding. A consumer whose independently authored wire contract uses JSON numbers must explicitly select `number`. This is an encoding choice, not permission to relax a schema's numeric bounds. The numeric acceptance fixture bounds its uint64 field to the exact JavaScript-safe integer range; it rejects strings, negatives, and values above that range. The validator records the selected encoding in its report.

## Evidence

The repository CI executes:

1. standard-library unit tests for path isolation, symlink rejection, Draft 2020-12 enforcement, Rust preconditions, mutation detection, and receipt generation;
2. a convergent TypeSpec/JSON Schema fixture through the real compiler-backed validator pinned by immutable commit;
3. a deliberately drifted required-field fixture that must fail closed;
4. a bounded JSON-number uint64 fixture that passes only with explicit numeric encoding, including recorded valid/invalid boundary instances;
5. the same numeric contract with the wrong string encoding, which must report actual verdict divergences, not merely a failed compiler or missing file;
6. retained JSON, SARIF, generated comparison schema, Contract IR, source-digest, and sibling-org receipt artifacts.

CI also checks that required-field drift produced semantic divergence, that an admissible Contract IR never claims editable authority, and that checked-in fixture bytes remained unchanged. Unique scratch directories live under ignored `tmp/`; no fixed directory is recursively deleted. Existing Python guard behavior is retained, with Rust migration and parity-test ownership tracked under DEN-4080.

The machine-readable plan remains in `suite.json`. `contract-only` remains accurate until CI exercises real `ores-chat` and isolated `ores-chat-test` deployments with distinct credentials for every trust plane, correlation checks, and redacted hosted evidence.
