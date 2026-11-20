from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "assets/changes-template.md").read_text(encoding="utf-8")
STATE = (ROOT / "assets/state-contract.md").read_text(encoding="utf-8")


class RoadmapContractTests(unittest.TestCase):
    def test_local_fresh_project_has_no_sdd_gate(self):
        prechecks = SKILL.split("### Pre-checks obligatorios", 1)[1].split("### Output", 1)[0]
        self.assertNotRegex(prechecks, r"(?i)openspec")
        self.assertNotRegex(TEMPLATE, r"(?i)openspec|/opsx:|skill de propuesta")
        self.assertNotRegex(SKILL, r"(?i)openspec init|/opsx:")

    def test_existing_repo_does_not_require_scaffolding(self):
        self.assertRegex(SKILL, r"(?im)^1\. .*infra.*solo si falta")
        self.assertNotRegex(SKILL, r"C-01 es siempre")
        self.assertIn("primer item elegible", SKILL)

    def test_roadmap_keeps_task_identity_and_scope_when_linked(self):
        self.assertIn("No renumeres IDs existentes", SKILL)
        self.assertIn("alcance y las dependencias", TEMPLATE)
        self.assertIn("responsable y el estado operativo", TEMPLATE)

    def test_roadmap_defers_remote_operations_to_task_management(self):
        self.assertIn("`active-roadmap`", SKILL)
        self.assertIn("vista previa y confirmación", SKILL)
        self.assertIn(".active-stack/roadmap-state.json", TEMPLATE)

    def test_resume_keeps_existing_roadmap_and_records_its_path(self):
        self.assertRegex(SKILL, r"(?im)^- .*`reuse`.*`CHANGES\.md`.*sin escribir")
        self.assertRegex(STATE, r'"changes_file": "CHANGES.md"')
        self.assertRegex(STATE, r'"source": "CHANGES.md"')
        self.assertIsNone(re.search(r"version == 2|version != 2", STATE))

    def test_team_mode_fields_verifier_and_split_are_documented(self):
        for field in ("Resultado", "Depende de", "Zona", "Requiere", "Modo", "Responsable"):
            self.assertIn("**%s**" % field, TEMPLATE)
            self.assertIn("`%s`" % field, SKILL)
        self.assertIn("fichas.sh", SKILL)
        self.assertIn("fichas.bat", SKILL)
        self.assertIn("apply --file", SKILL)
        self.assertRegex(SKILL, r"(?i)OpenCode.*Antigravity")
        self.assertIn("active-delivery", SKILL)

    def test_old_roadmaps_keep_working(self):
        self.assertRegex(SKILL, r"(?i)sin estos campos.*siguen")


if __name__ == "__main__":
    unittest.main()
