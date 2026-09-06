#!/usr/bin/env python3
"""Guard independently authored contract trees before and after sibling-org validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, NoReturn

REPORT_SCHEMA = "ores.typespec-json-schema-validator.report/v1"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
RECEIPT_SCHEMA = "ores-chat-test.api-contract-e2e.receipt/v1"
SNAPSHOT_SCHEMA = "ores-chat-test.api-contract-e2e.snapshot/v1"


class ContractGuardError(RuntimeError):
    """Raised when the acceptance boundary cannot be proven safely."""


def fail(message: str) -> NoReturn:
    raise ContractGuardError(message)


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    fail(f"expected true or false, got {value!r}")


def resolve_source_root(raw: str) -> Path:
    root = Path(raw).expanduser()
    try:
        resolved = root.resolve(strict=True)
    except FileNotFoundError:
        fail(f"source root does not exist: {root}")
    if not resolved.is_dir():
        fail(f"source root is not a directory: {resolved}")
    if root.is_symlink():
        fail(f"source root must not be a symlink: {root}")
    return resolved


def resolve_under(
    root: Path,
    raw: str,
    label: str,
    *,
    must_exist: bool = True,
    require_relative: bool = True,
) -> Path:
    candidate_input = Path(raw).expanduser()
    if require_relative and candidate_input.is_absolute():
        fail(f"{label} must be relative to the source root: {candidate_input}")
    candidate = candidate_input if candidate_input.is_absolute() else root / candidate_input
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        fail(f"{label} does not exist: {candidate}")
    if not resolved.is_relative_to(root):
        fail(f"{label} escapes the source root: {resolved}")
    if must_exist and candidate.is_symlink():
        fail(f"{label} must not be a symlink: {candidate}")
    return resolved


def overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def regular_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if root.is_symlink():
        fail(f"authored tree must not be a symlink: {root}")
    files: list[Path] = []
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dir_names):
            child = directory_path / name
            if child.is_symlink():
                fail(f"authored tree contains a symlinked directory: {child}")
        for name in sorted(file_names):
            child = directory_path / name
            if child.is_symlink():
                fail(f"authored tree contains a symlinked file: {child}")
            metadata = child.stat()
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"authored tree contains a non-regular file: {child}")
            if child.suffix.lower() in suffixes:
                files.append(child)
    return sorted(files)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_entry(root: Path, label: str, path: Path) -> dict[str, Any]:
    metadata = path.stat()
    return {
        "authority": label,
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size": metadata.st_size,
    }


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{label} is not valid UTF-8 JSON: {path}: {error}")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    parsed = load_json(path, label)
    if not isinstance(parsed, dict):
        fail(f"{label} must be a JSON object: {path}")
    return parsed


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def locate_inputs(
    root: Path,
    typespec_input: str,
    schema_input: str,
    instances_input: str,
    evidence_input: str,
    run_rust_tests: bool,
) -> tuple[Path, Path, Path, Path]:
    typespec = resolve_under(root, typespec_input, "TypeSpec authority")
    schema = resolve_under(root, schema_input, "JSON Schema authority")
    instances = resolve_under(root, instances_input, "instance corpus")
    evidence = resolve_under(
        root, evidence_input, "evidence directory", must_exist=False
    )

    if not typespec.is_file() or typespec.suffix.lower() != ".tsp":
        fail(f"TypeSpec authority must be a .tsp entry file: {typespec}")
    if not schema.is_dir():
        fail(f"JSON Schema authority must be a directory: {schema}")
    if not instances.is_dir():
        fail(f"instance corpus must be a directory: {instances}")

    authored_roots = (typespec.parent, schema, instances)
    for authored in authored_roots:
        if overlaps(authored, evidence):
            fail(
                "generated evidence must be isolated from authored inputs: "
                f"{evidence} overlaps {authored}"
            )

    if run_rust_tests:
        manifest = root / "Cargo.toml"
        lockfile = root / "Cargo.lock"
        if not manifest.is_file() or manifest.is_symlink():
            fail(f"Rust parity was requested but Cargo.toml is absent or unsafe: {manifest}")
        if not lockfile.is_file() or lockfile.is_symlink():
            fail(f"Rust parity was requested but Cargo.lock is absent or unsafe: {lockfile}")

    return typespec, schema, instances, evidence


def inventory(
    root: Path, typespec: Path, schema: Path, instances: Path
) -> list[dict[str, Any]]:
    type_files = regular_files(typespec.parent, (".tsp",))
    schema_files = regular_files(schema, (".json",))
    instance_files = regular_files(instances, (".json",))

    if typespec not in type_files:
        fail(f"TypeSpec entry file was not inventoried: {typespec}")
    if not schema_files:
        fail(f"JSON Schema authority contains no JSON files: {schema}")
    if not instance_files:
        fail(f"instance corpus contains no JSON files: {instances}")

    for path in schema_files:
        document = load_json_object(path, "JSON Schema authority")
        if document.get("$schema") != DRAFT_2020_12:
            fail(
                "JSON Schema authority must declare Draft 2020-12 exactly: "
                f"{path.relative_to(root)}"
            )

    for path in instance_files:
        load_json(path, "instance corpus entry")

    entries = [
        *(manifest_entry(root, "typespec", path) for path in type_files),
        *(manifest_entry(root, "json-schema", path) for path in schema_files),
        *(manifest_entry(root, "instances", path) for path in instance_files),
    ]
    return sorted(entries, key=lambda entry: (entry["authority"], entry["path"]))


def manifest_digest(entries: Iterable[dict[str, Any]]) -> str:
    encoded = json.dumps(list(entries), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def snapshot(arguments: argparse.Namespace) -> int:
    root = resolve_source_root(arguments.source_root)
    run_rust_tests = parse_bool(arguments.run_rust_tests)
    typespec, schema, instances, evidence = locate_inputs(
        root,
        arguments.typespec,
        arguments.schema,
        arguments.instances,
        arguments.evidence_root,
        run_rust_tests,
    )
    entries = inventory(root, typespec, schema, instances)
    manifest_path = resolve_under(
        root, arguments.manifest, "snapshot manifest", must_exist=False
    )
    if not manifest_path.is_relative_to(evidence):
        fail(f"snapshot manifest must be inside the evidence directory: {manifest_path}")

    document = {
        "schema": SNAPSHOT_SCHEMA,
        "sourceRoot": ".",
        "typespecEntry": typespec.relative_to(root).as_posix(),
        "jsonSchemaRoot": schema.relative_to(root).as_posix(),
        "instancesRoot": instances.relative_to(root).as_posix(),
        "runRustTests": run_rust_tests,
        "entries": entries,
        "digest": manifest_digest(entries),
    }
    atomic_write_json(manifest_path, document)
    print(f"snapshotted {len(entries)} authored contract files")
    return 0


def verify(arguments: argparse.Namespace) -> int:
    root = resolve_source_root(arguments.source_root)
    run_rust_tests = parse_bool(arguments.run_rust_tests)
    typespec, schema, instances, evidence = locate_inputs(
        root,
        arguments.typespec,
        arguments.schema,
        arguments.instances,
        arguments.evidence_root,
        run_rust_tests,
    )
    manifest_path = resolve_under(root, arguments.manifest, "snapshot manifest")
    if not manifest_path.is_relative_to(evidence):
        fail(f"snapshot manifest must be inside the evidence directory: {manifest_path}")

    manifest = load_json_object(manifest_path, "snapshot manifest")
    if manifest.get("schema") != SNAPSHOT_SCHEMA:
        fail("snapshot manifest has an unsupported schema")
    before = manifest.get("entries")
    if not isinstance(before, list):
        fail("snapshot manifest entries must be an array")
    after = inventory(root, typespec, schema, instances)
    if before != after or manifest.get("digest") != manifest_digest(after):
        fail("an independently authored contract input changed during validation")

    report_path = resolve_under(root, arguments.report, "validator report")
    sarif_path = resolve_under(root, arguments.sarif, "validator SARIF")
    if not report_path.is_relative_to(evidence) or not sarif_path.is_relative_to(
        evidence
    ):
        fail("validator evidence must remain inside the isolated evidence directory")

    report = load_json_object(report_path, "validator report")
    if report.get("schema") != REPORT_SCHEMA:
        fail("validator report has an unsupported schema")
    if report.get("status") != "passed":
        fail(f"validator report did not pass: {report.get('status')!r}")
    if report.get("zeroUnexplainedFindings") is not True:
        fail("validator report contains unexplained findings")
    if report.get("findings") != []:
        fail("validator report must contain no findings after a successful run")

    sarif = load_json_object(sarif_path, "validator SARIF")
    if sarif.get("version") != "2.1.0":
        fail("validator SARIF must declare version 2.1.0")

    receipt_path = resolve_under(
        root, arguments.receipt, "test-org receipt", must_exist=False
    )
    if not receipt_path.is_relative_to(evidence):
        fail("test-org receipt must remain inside the isolated evidence directory")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "passed",
        "sourceDigest": manifest_digest(after),
        "validatorRunId": report.get("runId"),
        "validatorReport": report_path.relative_to(root).as_posix(),
        "validatorSarif": sarif_path.relative_to(root).as_posix(),
        "runRustTests": run_rust_tests,
        "authorities": {
            "typespec": typespec.relative_to(root).as_posix(),
            "jsonSchema": schema.relative_to(root).as_posix(),
            "instances": instances.relative_to(root).as_posix(),
        },
    }
    atomic_write_json(receipt_path, receipt)
    print(
        "verified immutable peer authorities; "
        f"receipt={receipt_path.relative_to(root)}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--source-root", required=True)
    shared.add_argument("--typespec", required=True)
    shared.add_argument("--schema", required=True)
    shared.add_argument("--instances", required=True)
    shared.add_argument("--evidence-root", required=True)
    shared.add_argument("--manifest", required=True)
    shared.add_argument("--run-rust-tests", required=True)

    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("snapshot", parents=[shared])

    verify_parser = subcommands.add_parser("verify", parents=[shared])
    verify_parser.add_argument("--report", required=True)
    verify_parser.add_argument("--sarif", required=True)
    verify_parser.add_argument("--receipt", required=True)
    return result


def main(argv: list[str]) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "snapshot":
            return snapshot(arguments)
        return verify(arguments)
    except ContractGuardError as error:
        print(f"api-contract-e2e: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
