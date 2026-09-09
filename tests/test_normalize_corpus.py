import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "normalize_corpus.py"


class NormalizeCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.schema = self.root / "contracts" / "json-schema"
        self.fixtures = self.root / "contracts" / "fixtures"
        self.schema.mkdir(parents=True)
        self.fixtures.mkdir(parents=True)
        (self.schema / "authored.schema.json").write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$defs": {
                        "Widget": {"type": "object"},
                        "AuditEvent": {"type": "object"},
                        "SupportEvent": {"type": "object"},
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_normalizer(self):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source-root",
                str(self.root),
                "--instances",
                "contracts/fixtures",
                "--schema",
                "contracts/json-schema",
                "--evidence-root",
                ".evidence",
                "--output",
                ".evidence/normalized-instances",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def manifest(self):
        return json.loads((self.root / ".evidence" / "normalized-corpus.json").read_text(encoding="utf-8"))

    def test_preserves_canonical_tjsv_expectation_layout(self):
        source = self.fixtures / "Widget" / "valid" / "minimal.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"name":"ok"}\n', encoding="utf-8")

        result = self.run_normalizer()

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / ".evidence" / "normalized-instances" / "Widget" / "valid" / "minimal.json"
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(self.manifest()["entries"][0]["target"], "Widget/valid/minimal.json")

    def test_routes_slice_model_fixture_by_schema_declaration(self):
        source = self.fixtures / "admin" / "AuditEvent.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"protocol":"test"}\n', encoding="utf-8")

        result = self.run_normalizer()

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / ".evidence" / "normalized-instances" / "AuditEvent" / "admin--AuditEvent.json"
        self.assertEqual(target.read_bytes(), source.read_bytes())
        entry = self.manifest()["entries"][0]
        self.assertEqual(entry["declaration"], "AuditEvent")
        self.assertEqual(entry["source"], "admin/AuditEvent.json")

    def test_routes_only_explicitly_reviewed_legacy_fixture(self):
        source = self.fixtures / "ticket-created.json"
        source.write_text('{"event_type":"ticket_created"}\n', encoding="utf-8")

        result = self.run_normalizer()

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / ".evidence" / "normalized-instances" / "SupportEvent" / "legacy--ticket-created.json"
        self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_refuses_unknown_slice_fixture_instead_of_guessing(self):
        source = self.fixtures / "admin" / "MissingDeclaration.json"
        source.parent.mkdir(parents=True)
        source.write_text('{}\n', encoding="utf-8")

        result = self.run_normalizer()

        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown declaration", result.stderr)

    def test_refuses_extra_nesting_under_a_canonical_declaration(self):
        source = self.fixtures / "Widget" / "valid" / "nested" / "payload.json"
        source.parent.mkdir(parents=True)
        source.write_text('{}\n', encoding="utf-8")

        result = self.run_normalizer()

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported canonical corpus nesting", result.stderr)


if __name__ == "__main__":
    unittest.main()
