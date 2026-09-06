#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


def load_guard() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "contract_guard.py"
    spec = importlib.util.spec_from_file_location("contract_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load contract_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load_guard()


class ContractGuardTests(unittest.TestCase):
    def make_source(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "contracts/typespec").mkdir(parents=True)
        (root / "contracts/json-schema").mkdir(parents=True)
        (root / "contracts/fixtures/Widget/valid").mkdir(parents=True)
        (root / "contracts/typespec/main.tsp").write_text(
            "model Widget { name: string; }\n", encoding="utf-8"
        )
        (root / "contracts/json-schema/widget.schema.json").write_text(
            json.dumps(
                {
                    "$schema": guard.DRAFT_2020_12,
                    "$defs": {
                        "Widget": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "contracts/fixtures/Widget/valid/minimal.json").write_text(
            '{"name":"test"}\n', encoding="utf-8"
        )
        return root

    def common(self, root: Path) -> list[str]:
        return [
            "--source-root",
            str(root),
            "--typespec",
            "contracts/typespec/main.tsp",
            "--schema",
            "contracts/json-schema",
            "--instances",
            "contracts/fixtures",
            "--evidence-root",
            ".ores-chat-test/api-contract-e2e",
            "--manifest",
            ".ores-chat-test/api-contract-e2e/authored-inputs.json",
            "--run-rust-tests",
            "false",
        ]

    def test_snapshot_and_verify_emit_a_stable_receipt(self) -> None:
        root = self.make_source()
        self.assertEqual(guard.main(["snapshot", *self.common(root)]), 0)
        evidence = root / ".ores-chat-test/api-contract-e2e"
        (evidence / "validator-report.json").write_text(
            json.dumps(
                {
                    "schema": guard.REPORT_SCHEMA,
                    "runId": "a" * 64,
                    "status": "passed",
                    "zeroUnexplainedFindings": True,
                    "findings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (evidence / "validator.sarif").write_text(
            '{"version":"2.1.0","runs":[]}\n', encoding="utf-8"
        )
        result = guard.main(
            [
                "verify",
                *self.common(root),
                "--report",
                ".ores-chat-test/api-contract-e2e/validator-report.json",
                "--sarif",
                ".ores-chat-test/api-contract-e2e/validator.sarif",
                "--receipt",
                ".ores-chat-test/api-contract-e2e/receipt.json",
            ]
        )
        self.assertEqual(result, 0)
        receipt = json.loads((evidence / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], guard.RECEIPT_SCHEMA)
        self.assertEqual(receipt["status"], "passed")
        self.assertFalse(receipt["runRustTests"])

    def test_mutated_authority_is_rejected(self) -> None:
        root = self.make_source()
        self.assertEqual(guard.main(["snapshot", *self.common(root)]), 0)
        schema = root / "contracts/json-schema/widget.schema.json"
        schema.write_text(schema.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(
            guard.ContractGuardError, "changed during validation"
        ):
            arguments = guard.build_parser().parse_args(
                [
                    "verify",
                    *self.common(root),
                    "--report",
                    ".ores-chat-test/api-contract-e2e/missing-report.json",
                    "--sarif",
                    ".ores-chat-test/api-contract-e2e/missing.sarif",
                    "--receipt",
                    ".ores-chat-test/api-contract-e2e/receipt.json",
                ]
            )
            guard.verify(arguments)

    def test_evidence_overlap_is_rejected(self) -> None:
        root = self.make_source()
        arguments = guard.build_parser().parse_args(
            [
                "snapshot",
                *[
                    value
                    for pair in zip(self.common(root)[::2], self.common(root)[1::2])
                    for value in pair
                    if pair[0] not in {"--evidence-root", "--manifest"}
                ],
                "--evidence-root",
                "contracts/json-schema/generated",
                "--manifest",
                "contracts/json-schema/generated/authored-inputs.json",
            ]
        )
        with self.assertRaisesRegex(
            guard.ContractGuardError, "generated evidence must be isolated"
        ):
            guard.snapshot(arguments)

    def test_non_2020_12_schema_is_rejected(self) -> None:
        root = self.make_source()
        schema = root / "contracts/json-schema/widget.schema.json"
        document = json.loads(schema.read_text(encoding="utf-8"))
        document["$schema"] = "http://json-schema.org/draft-07/schema#"
        schema.write_text(json.dumps(document) + "\n", encoding="utf-8")
        arguments = guard.build_parser().parse_args(
            ["snapshot", *self.common(root)]
        )
        with self.assertRaisesRegex(guard.ContractGuardError, "Draft 2020-12"):
            guard.snapshot(arguments)

    def test_rust_mode_requires_manifest_and_lockfile(self) -> None:
        root = self.make_source()
        common = self.common(root)
        common[common.index("false")] = "true"
        arguments = guard.build_parser().parse_args(["snapshot", *common])
        with self.assertRaisesRegex(guard.ContractGuardError, "Cargo.toml"):
            guard.snapshot(arguments)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks unavailable")
    def test_symlinked_authored_file_is_rejected(self) -> None:
        root = self.make_source()
        target = root / "outside.tsp"
        target.write_text("model Outside {}\n", encoding="utf-8")
        link = root / "contracts/typespec/imported.tsp"
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        arguments = guard.build_parser().parse_args(["snapshot", *self.common(root)])
        with self.assertRaisesRegex(guard.ContractGuardError, "symlinked file"):
            guard.snapshot(arguments)


if __name__ == "__main__":
    unittest.main()
