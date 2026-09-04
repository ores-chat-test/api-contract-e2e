# API contract end-to-end suite

Status: **contract-only**. This repository defines the executable acceptance boundary for the ORES Chat public, customer, administrator, and internal HTTP APIs. It does not yet claim a deployed end-to-end pass.

The suite must eventually execute against both `ores-chat` deployments and isolated `ores-chat-test` fixtures. Promotion to `live` requires real endpoint execution, distinct credentials for every trust plane, correlation checks, and retained hosted evidence with secrets and message content redacted.

The machine-readable plan is in `suite.json` and is validated by the organization policy action pinned to an immutable commit.