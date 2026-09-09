#!/usr/bin/env python3
"""Build a declaration-indexed TJSV corpus from immutable authored fixture files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import NoReturn

MANIFEST_SCHEMA = "ores-chat-test.api-contract-e2e.normalized-corpus/v1"
EXPECTATION_DIRECTORIES = {"valid", "invalid"}
LEGACY_DECLARATIONS = {
    "ticket-created.json": "SupportEvent",
}


class CorpusError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise CorpusError(message)


def resolve_root(raw: str) -> Path:
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        fail(f"source root does not exist: {path}")
    if not resolved.is_dir() or path.is_symlink():
        fail(f"source root must be a real directory: {path}")
    return resolved


def resolve_under(root: Path, raw: str, label: str, *, must_exist: bool = True) -> Path:
    supplied = Path(raw).expanduser()
    if supplied.is_absolute():
        fail(f"{label} must be relative to source root: {supplied}")
    candidate = root / supplied
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        fail(f"{label} does not exist: {candidate}")
    if not resolved.is_relative_to(root):
        fail(f"{label} escapes source root: {resolved}")
    if must_exist and candidate.is_symlink():
        fail(f"{label} must not be a symlink: {candidate}")
    return resolved


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_json_files(root: Path, label: str) -> list[Path]:
    if root.is_symlink():
        fail(f"{label} must not be a symlink: {root}")
    files: list[Path] = []
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dir_names):
            child = directory_path / name
            if child.is_symlink():
                fail(f"{label} contains symlinked directory: {child}")
        for name in sorted(file_names):
            child = directory_path / name
            if child.is_symlink():
                fail(f"{label} contains symlinked file: {child}")
            metadata = child.stat()
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"{label} contains non-regular file: {child}")
            if child.suffix.lower() == ".json":
                files.append(child)
    return sorted(files)


def schema_declarations(schema_root: Path) -> set[str]:
    declarations: set[str] = set()
    files = regular_json_files(schema_root, "JSON Schema authority")
    if not files:
        fail(f"JSON Schema authority contains no JSON files: {schema_root}")
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
            fail(f"JSON Schema authority is not valid UTF-8 JSON: {path}: {error}")
        if not isinstance(document, dict):
            fail(f"JSON Schema authority must be an object: {path}")
        definitions = document.get("$defs", {})
        if not isinstance(definitions, dict):
            fail(f"JSON Schema authority $defs must be an object: {path}")
        declarations.update(name for name in definitions if isinstance(name, str) and name)
    if not declarations:
        fail("JSON Schema authority exposes no declarations in $defs")
    return declarations


def fixture_files(root: Path) -> list[Path]:
    files = regular_json_files(root, "fixture tree")
    if not files:
        fail(f"fixture tree contains no JSON files: {root}")
    return files


def target_relative(instances: Path, fixture: Path, declarations: set[str]) -> tuple[str, Path]:
    relative = fixture.relative_to(instances)
    parts = relative.parts

    # Already canonical TJSV corpus. Keep declared valid/invalid intent and any
    # unclassified declaration-level file exactly as authored.
    if parts and parts[0] in declarations:
        if len(parts) == 2 or (len(parts) == 3 and parts[1] in EXPECTATION_DIRECTORIES):
            return parts[0], relative
        fail(
            "unsupported canonical corpus nesting; expected "
            "<Declaration>/*.json or <Declaration>/{valid,invalid}/*.json: "
            f"{relative.as_posix()}"
        )

    # ORES Chat production layout: <slice>/<Declaration>.json. The JSON Schema
    # authority proves that the filename is a real declaration before routing it.
    if len(parts) == 2 and fixture.stem in declarations:
        declaration = fixture.stem
        return declaration, Path(declaration) / f"{parts[0]}--{fixture.name}"

    # Explicitly reviewed legacy single-file layouts. Never guess a declaration.
    if len(parts) == 1 and relative.name in LEGACY_DECLARATIONS:
        declaration = LEGACY_DECLARATIONS[relative.name]
        if declaration not in declarations:
            fail(
                f"legacy fixture mapping targets declaration absent from JSON Schema authority: "
                f"{relative.name} -> {declaration}"
            )
        return declaration, Path(declaration) / f"legacy--{fixture.name}"

    fail(
        "unsupported authored fixture layout or unknown declaration; expected canonical TJSV "
        "layout, <slice>/<Declaration>.json backed by JSON Schema $defs, or an explicit legacy "
        f"mapping: {relative.as_posix()}"
    )


def normalize(arguments: argparse.Namespace) -> int:
    root = resolve_root(arguments.source_root)
    instances = resolve_under(root, arguments.instances, "authored fixture root")
    schema = resolve_under(root, arguments.schema, "JSON Schema authority")
    evidence = resolve_under(root, arguments.evidence_root, "evidence root", must_exist=False)
    output = resolve_under(root, arguments.output, "normalized corpus output", must_exist=False)

    if not instances.is_dir():
        fail(f"authored fixture root must be a directory: {instances}")
    if not schema.is_dir():
        fail(f"JSON Schema authority must be a directory: {schema}")
    if not output.is_relative_to(evidence) or output == evidence:
        fail(f"normalized corpus must be a child of the evidence root: {output}")
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            fail(f"normalized corpus output exists but is unsafe: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=False)

    declarations = schema_declarations(schema)
    entries: list[dict[str, str]] = []
    for fixture in fixture_files(instances):
        relative = fixture.relative_to(instances)
        try:
            json.loads(fixture.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
            fail(f"fixture is not valid UTF-8 JSON: {relative.as_posix()}: {error}")

        declaration, routed = target_relative(instances, fixture, declarations)
        target = output / routed
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            fail(f"normalized corpus collision: {target.relative_to(root)}")
        shutil.copyfile(fixture, target)
        source_digest = sha256(fixture)
        if source_digest != sha256(target):
            fail(f"normalized corpus byte-copy mismatch: {relative.as_posix()}")
        entries.append(
            {
                "source": relative.as_posix(),
                "declaration": declaration,
                "target": routed.as_posix(),
                "sha256": source_digest,
            }
        )

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "source": instances.relative_to(root).as_posix(),
        "jsonSchemaAuthority": schema.relative_to(root).as_posix(),
        "output": output.relative_to(root).as_posix(),
        "entries": sorted(entries, key=lambda entry: (entry["declaration"], entry["source"])),
    }
    manifest_path = output.parent / "normalized-corpus.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"normalized {len(entries)} authored fixtures into declaration-indexed corpus")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source-root", required=True)
    result.add_argument("--instances", required=True)
    result.add_argument("--schema", required=True)
    result.add_argument("--evidence-root", required=True)
    result.add_argument("--output", required=True)
    return result


def main(argv: list[str]) -> int:
    try:
        return normalize(parser().parse_args(argv))
    except CorpusError as error:
        print(f"api-contract-e2e corpus: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
