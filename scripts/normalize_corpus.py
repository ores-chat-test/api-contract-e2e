#!/usr/bin/env python3
"""Build a declaration-indexed TJSV corpus from immutable ORES Chat fixture files."""

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


def fixture_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dir_names):
            child = directory_path / name
            if child.is_symlink():
                fail(f"fixture tree contains symlinked directory: {child}")
        for name in sorted(file_names):
            child = directory_path / name
            if child.is_symlink():
                fail(f"fixture tree contains symlinked file: {child}")
            metadata = child.stat()
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"fixture tree contains non-regular file: {child}")
            if child.suffix.lower() == ".json":
                files.append(child)
    if not files:
        fail(f"fixture tree contains no JSON files: {root}")
    return sorted(files)


def declaration_for(instances: Path, fixture: Path) -> str:
    relative = fixture.relative_to(instances)
    if len(relative.parts) == 2:
        declaration = fixture.stem
    elif len(relative.parts) == 1 and relative.name in LEGACY_DECLARATIONS:
        declaration = LEGACY_DECLARATIONS[relative.name]
    else:
        fail(
            "unsupported authored fixture layout; expected <slice>/<Declaration>.json "
            f"or an explicit legacy mapping: {relative.as_posix()}"
        )
    if not declaration or any(character in declaration for character in "/\\"):
        fail(f"unsafe declaration name derived from fixture: {relative.as_posix()}")
    return declaration


def normalize(arguments: argparse.Namespace) -> int:
    root = resolve_root(arguments.source_root)
    instances = resolve_under(root, arguments.instances, "authored fixture root")
    evidence = resolve_under(root, arguments.evidence_root, "evidence root", must_exist=False)
    output = resolve_under(root, arguments.output, "normalized corpus output", must_exist=False)

    if not instances.is_dir():
        fail(f"authored fixture root must be a directory: {instances}")
    if not evidence.is_relative_to(root):
        fail("evidence root must remain below source root")
    if not output.is_relative_to(evidence):
        fail(f"normalized corpus must stay inside evidence root: {output}")
    if output == evidence:
        fail("normalized corpus output must not replace the whole evidence root")
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            fail(f"normalized corpus output exists but is unsafe: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=False)

    entries: list[dict[str, str]] = []
    for fixture in fixture_files(instances):
        relative = fixture.relative_to(instances)
        declaration = declaration_for(instances, fixture)
        try:
            json.loads(fixture.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
            fail(f"fixture is not valid UTF-8 JSON: {relative.as_posix()}: {error}")

        origin = relative.parts[0] if len(relative.parts) > 1 else "legacy"
        target_dir = output / declaration
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{origin}--{fixture.name}"
        if target.exists():
            fail(f"normalized corpus collision: {target.relative_to(root)}")
        shutil.copyfile(fixture, target)
        if sha256(fixture) != sha256(target):
            fail(f"normalized corpus byte-copy mismatch: {relative.as_posix()}")
        entries.append(
            {
                "source": relative.as_posix(),
                "declaration": declaration,
                "target": target.relative_to(output).as_posix(),
                "sha256": sha256(fixture),
            }
        )

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "source": instances.relative_to(root).as_posix(),
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
