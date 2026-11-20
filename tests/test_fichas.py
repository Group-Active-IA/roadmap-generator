"""Pruebas de [EQ] Fase 2: campos de ficha, verificador y reparto (fichas.py).

Se corren desde la raiz del repo con `python -B -m unittest discover -s tests`.
"""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "fichas.py"

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("fichas", SCRIPT)
fichas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fichas)


def ficha(fid, estado="[ ]", **fields):
    """Arma un bloque de ficha en el formato de CHANGES.md."""
    lines = ["### [%s] `ficha-%s`" % (fid, fid.lower()), "- **Estado**: `%s` pendiente" % estado]
    for key, value in fields.items():
        lines.append("- **%s**: %s" % (key.replace("_", " "), value))
    lines.append("- **Governance**: MEDIO")
    return "\n".join(lines) + "\n"


def roadmap(*blocks):
    return "# CHANGES — Secuencia de Implementación\n\n## FASE 0 — Base\n\n" + "\n---\n\n".join(blocks)


def team_ficha(fid, deps="ninguna", zona=None, owner=None, estado="[ ]", resultado="Algo verificable"):
    fields = {"Resultado": resultado, "Depende_de": deps, "Requiere": "claude o codex", "Modo": "odd"}
    if zona:
        fields["Zona"] = zona
    if owner:
        fields["Responsable"] = owner
    return ficha(fid, estado=estado, **fields)


class Project:
    """Proyecto temporal con CHANGES.md y, opcionalmente, team.json."""

    def __init__(self, text, team=None):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "CHANGES.md").write_text(text, encoding="utf-8")
        if team is not None:
            (self.dir / ".active-stack").mkdir()
            (self.dir / ".active-stack" / "team.json").write_text(json.dumps(team), encoding="utf-8")

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


TEAM4 = {"size": 4, "members": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}, {"id": "m4"}]}


def verify(text, team=None, validator=None):
    project = Project(text, team)
    try:
        return fichas.verify_project(str(project.dir), validator=validator)
    finally:
        project.close()


def legacy_roadmap():
    """Las fichas de ejemplo de la plantilla: formato viejo, sin campos del modo equipo."""
    text = (ROOT / "assets" / "changes-template.md").read_text(encoding="utf-8")
    start = text.index("## FASE 0")
    end = text.index("### [C-{NN}]")
    return text[start:end]


class ParseTest(unittest.TestCase):
    def test_reads_ids_fields_and_state(self):
        parsed = fichas.parse_changes(roadmap(
            team_ficha("C-01", zona="`src/a/`"),
            team_ficha("C-02", deps="C-01", estado="[x]"),
        ))
        self.assertEqual([f["id"] for f in parsed], ["C-01", "C-02"])
        self.assertEqual(parsed[0]["fields"]["Zona"], "`src/a/`")
        self.assertEqual(parsed[1]["fields"]["Depende de"], "C-01")
        self.assertEqual(parsed[0]["state"], "pending")
        self.assertEqual(parsed[1]["state"], "done")

    def test_started_state_is_anything_but_pending_or_done(self):
        parsed = fichas.parse_changes(roadmap(team_ficha("C-01", estado="[~]")))
        self.assertEqual(parsed[0]["state"], "started")

    def test_template_shipped_with_the_skill_parses(self):
        parsed = fichas.parse_changes(legacy_roadmap())
        self.assertGreaterEqual(len(parsed), 4)
        self.assertTrue(all(f["id"].startswith("C-") for f in parsed))


class VerifierTest(unittest.TestCase):
    """[EQ] Fase 2: dependencias reales, ficha vieja intacta, verificador sin red."""

    def test_valid_team_roadmap_passes(self):
        result = verify(roadmap(
            team_ficha("C-01", zona="src/a", owner="m1"),
            team_ficha("C-02", deps="C-01", zona="src/b", owner="m2"),
        ), team=TEAM4)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["errors"], [])

    def test_cycle_is_an_error_naming_the_path(self):
        result = verify(roadmap(
            team_ficha("C-01", deps="C-03"),
            team_ficha("C-02", deps="C-01"),
            team_ficha("C-03", deps="C-02"),
        ))
        self.assertFalse(result["ok"])
        cycle = [e for e in result["errors"] if "ciclo" in e]
        self.assertEqual(len(cycle), 1, result["errors"])
        for fid in ("C-01", "C-02", "C-03"):
            self.assertIn(fid, cycle[0])

    def test_self_dependency_is_a_cycle(self):
        result = verify(roadmap(team_ficha("C-01", deps="C-01")))
        self.assertTrue(any("ciclo" in e and "C-01" in e for e in result["errors"]), result)

    def test_unknown_dependency_is_an_error(self):
        result = verify(roadmap(team_ficha("C-01", deps="C-99")))
        self.assertFalse(result["ok"])
        self.assertTrue(any("C-99" in e for e in result["errors"]), result)

    def test_duplicated_id_is_an_error(self):
        result = verify(roadmap(team_ficha("C-01"), team_ficha("C-01")))
        self.assertTrue(any("C-01" in e and "repetid" in e for e in result["errors"]), result)

    def test_overlapping_zones_in_parallel_are_a_warning_not_an_error(self):
        result = verify(roadmap(
            team_ficha("C-01", zona="`src/auth/`"),
            team_ficha("C-02", zona="src/auth/login.py, docs"),
        ))
        self.assertTrue(result["ok"], result)
        overlap = [w for w in result["warnings"] if "zona compartida" in w]
        self.assertEqual(len(overlap), 1, result["warnings"])
        self.assertIn("C-01", overlap[0])
        self.assertIn("C-02", overlap[0])

    def test_overlapping_zones_in_sequence_are_not_flagged(self):
        result = verify(roadmap(
            team_ficha("C-01", zona="src/auth"),
            team_ficha("C-02", deps="C-01", zona="src/auth"),
        ))
        self.assertFalse([w for w in result["warnings"] if "zona compartida" in w], result["warnings"])

    def test_similar_prefix_is_not_an_overlap(self):
        result = verify(roadmap(
            team_ficha("C-01", zona="src/auth"),
            team_ficha("C-02", zona="src/authz"),
        ))
        self.assertFalse([w for w in result["warnings"] if "zona compartida" in w], result["warnings"])

    def test_done_fichas_do_not_overlap(self):
        result = verify(roadmap(
            team_ficha("C-01", zona="src/auth", estado="[x]"),
            team_ficha("C-02", zona="src/auth"),
        ))
        self.assertFalse([w for w in result["warnings"] if "zona compartida" in w], result["warnings"])

    def test_chain_of_two_open_prs_is_allowed(self):
        result = verify(roadmap(
            team_ficha("C-01", deps="#10@abcdef1", estado="[~]"),
            team_ficha("C-02", deps="C-01, #11@abcdef2"),
        ))
        self.assertFalse([e for e in result["errors"] if "PR" in e], result["errors"])

    def test_chain_of_three_open_prs_is_an_error(self):
        result = verify(roadmap(
            team_ficha("C-01", deps="#10@abcdef1", estado="[~]"),
            team_ficha("C-02", deps="C-01, #11@abcdef2", estado="[~]"),
            team_ficha("C-03", deps="C-02, #12@abcdef3"),
        ))
        chain = [e for e in result["errors"] if "PR" in e and "C-03" in e]
        self.assertEqual(len(chain), 1, result["errors"])

    def test_merged_pr_without_sha_does_not_count_in_a_chain(self):
        result = verify(roadmap(
            team_ficha("C-01", deps="#10", estado="[~]"),
            team_ficha("C-02", deps="C-01, #11"),
            team_ficha("C-03", deps="C-02, #12"),
        ))
        self.assertFalse([e for e in result["errors"] if "PR" in e], result["errors"])

    def test_done_ficha_breaks_the_chain(self):
        result = verify(roadmap(
            team_ficha("C-01", deps="#10@abcdef1", estado="[x]"),
            team_ficha("C-02", deps="C-01, #11@abcdef2", estado="[~]"),
            team_ficha("C-03", deps="C-02, #12@abcdef3"),
        ))
        self.assertFalse([e for e in result["errors"] if "PR" in e], result["errors"])

    def test_team_ficha_without_result_is_an_error(self):
        result = verify(roadmap(ficha("C-01", Depende_de="ninguna", Modo="odd")))
        self.assertFalse(result["ok"])
        self.assertTrue(any("C-01" in e and "Resultado" in e for e in result["errors"]), result)

    def test_empty_result_is_an_error(self):
        result = verify(roadmap(team_ficha("C-01", resultado="  ")))
        self.assertTrue(any("C-01" in e and "Resultado" in e for e in result["errors"]), result)

    def test_unknown_owner_is_an_error(self):
        result = verify(roadmap(team_ficha("C-01", owner="m7")), team=TEAM4)
        self.assertFalse(result["ok"])
        self.assertTrue(any("m7" in e and "C-01" in e for e in result["errors"]), result)

    def test_owner_without_team_file_is_a_warning(self):
        result = verify(roadmap(team_ficha("C-01", owner="m1")))
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("team.json" in w for w in result["warnings"]), result)

    def test_unreadable_team_file_is_an_error(self):
        project = Project(roadmap(team_ficha("C-01", owner="m1")))
        try:
            (project.dir / ".active-stack").mkdir()
            (project.dir / ".active-stack" / "team.json").write_text("{no es json", encoding="utf-8")
            result = fichas.verify_project(str(project.dir), validator=None)
        finally:
            project.close()
        self.assertFalse(result["ok"])
        self.assertTrue(any("team.json" in e for e in result["errors"]), result)

    def test_old_roadmap_without_new_fields_stays_valid(self):
        text = legacy_roadmap()
        legacy = fichas.parse_changes(text)
        self.assertTrue(legacy)
        result = verify(text)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(any("Resultado" in w for w in result["warnings"]), result["warnings"])

    def test_legacy_dependencies_field_builds_the_graph(self):
        result = verify(roadmap(
            ficha("C-01", Dependencias="C-02"),
            ficha("C-02", Dependencias="C-01"),
        ))
        self.assertTrue(any("ciclo" in e for e in result["errors"]), result)

    def test_missing_changes_file_is_an_error(self):
        empty = Path(tempfile.mkdtemp())
        try:
            result = fichas.verify_project(str(empty), validator=None)
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("CHANGES.md" in e for e in result["errors"]), result)

    def test_verifier_does_not_use_the_network_or_git(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("import socket", "urllib", "http.client", "import requests", "subprocess", "os.system"):
            self.assertNotIn(forbidden, source)


class FieldValidatorTest(unittest.TestCase):
    """Reutiliza validate_ficha de active-delivery (R-06) sin copiar sus reglas."""

    def setUp(self):
        self.skills = Path(tempfile.mkdtemp())
        own = self.skills / "roadmap-generator"
        own.mkdir()
        shutil.copy(SCRIPT, own / "fichas.py")
        self.own = own

    def tearDown(self):
        shutil.rmtree(self.skills, ignore_errors=True)

    def write_team_py(self, body):
        delivery = self.skills / "active-delivery"
        delivery.mkdir()
        (delivery / "team.py").write_text(textwrap.dedent(body), encoding="utf-8")

    def test_finds_team_py_next_to_the_skill(self):
        self.write_team_py("""
            def validate_ficha(fields, team_doc=None):
                return {"ok": True, "errors": [], "warnings": []}
        """)
        validator = fichas.load_field_validator(str(self.own))
        self.assertIsNotNone(validator)

    def test_without_active_delivery_there_is_no_field_validator(self):
        self.assertIsNone(fichas.load_field_validator(str(self.own)))

    def test_field_errors_come_from_team_py(self):
        self.write_team_py("""
            def validate_ficha(fields, team_doc=None):
                if fields.get("Modo") not in (None, "odd", "sdd"):
                    return {"ok": False, "errors": ["Modo: %r no es valido" % fields["Modo"]], "warnings": []}
                return {"ok": True, "errors": [], "warnings": []}
        """)
        validator = fichas.load_field_validator(str(self.own))
        result = verify(roadmap(team_ficha("C-01").replace("**Modo**: odd", "**Modo**: xyz")), validator=validator)
        self.assertFalse(result["ok"])
        self.assertTrue(any(e.startswith("C-01: Modo") for e in result["errors"]), result)
        self.assertTrue(result["field_validation"])

    def test_dependencies_reach_team_py_without_backticks(self):
        self.write_team_py("""
            SEEN = []
            def validate_ficha(fields, team_doc=None):
                SEEN.append(dict(fields))
                return {"ok": True, "errors": [], "warnings": []}
        """)
        validator = fichas.load_field_validator(str(self.own))
        verify(roadmap(team_ficha("C-01"), team_ficha("C-02", deps="`C-01`")), validator=validator)
        seen = validator.__globals__["SEEN"]
        self.assertEqual(seen[1]["Depende de"], "C-01")
        self.assertEqual(seen[1]["id"], "C-02")

    def test_without_validator_the_result_says_fields_were_not_checked(self):
        result = verify(roadmap(team_ficha("C-01").replace("**Modo**: odd", "**Modo**: xyz")), validator=None)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["field_validation"])
        self.assertTrue(any("no disponible" in w for w in result["warnings"]), result["warnings"])

    def test_broken_team_py_is_reported_and_verification_continues(self):
        self.write_team_py("this is not python(")
        validator = fichas.load_field_validator(str(self.own))
        self.assertIsNone(validator)

    def test_loading_team_py_leaves_no_bytecode_behind(self):
        self.write_team_py("""
            def validate_ficha(fields, team_doc=None):
                return {"ok": True, "errors": [], "warnings": []}
        """)
        fichas.load_field_validator(str(self.own))
        self.assertFalse((self.skills / "active-delivery" / "__pycache__").exists())


def split(text, team=None, size=None):
    project = Project(text, team)
    try:
        return fichas.split_project(str(project.dir), size=size)
    finally:
        project.close()


class SplitTest(unittest.TestCase):
    """[EQ] Fase 2: reparto editable con N mayor y menor que el paralelismo real."""

    WIDE = roadmap(
        team_ficha("C-01"),
        team_ficha("C-02", deps="C-01"),
        team_ficha("C-03", deps="C-01"),
        team_ficha("C-04", deps="C-01"),
        team_ficha("C-05", deps="C-02, C-03, C-04"),
    )

    def test_waves_are_computed_from_dependencies(self):
        result = split(self.WIDE, team=TEAM4)
        self.assertTrue(result["ok"], result)
        self.assertEqual([[a["id"] for a in w["fichas"]] for w in result["waves"]],
                         [["C-01"], ["C-02", "C-03", "C-04"], ["C-05"]])
        self.assertEqual(result["parallelism"], 3)

    def test_more_people_than_parallel_work_does_not_invent_work(self):
        result = split(self.WIDE, team=TEAM4)
        wave2 = result["waves"][1]
        owners = [a["responsable"] for a in wave2["fichas"]]
        self.assertEqual(len(set(owners)), 3)
        self.assertEqual(len(wave2["sin_ficha"]), 1)
        assigned = sum(len(w["fichas"]) for w in result["waves"])
        self.assertEqual(assigned, 5)

    def test_fewer_people_than_parallel_work_share_a_wave(self):
        two = {"size": 2, "members": [{"id": "m1"}, {"id": "m2"}]}
        result = split(self.WIDE, team=two)
        wave2 = result["waves"][1]["fichas"]
        owners = [a["responsable"] for a in wave2]
        self.assertEqual(sorted(set(owners)), ["m1", "m2"])
        self.assertEqual(max(owners.count(o) for o in owners), 2)
        self.assertEqual(result["waves"][1]["sin_ficha"], [])

    def test_load_is_balanced_across_waves(self):
        three = {"size": 3, "members": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
        result = split(self.WIDE, team=three)
        load = {}
        for wave in result["waves"]:
            for item in wave["fichas"]:
                load[item["responsable"]] = load.get(item["responsable"], 0) + 1
        self.assertLessEqual(max(load.values()) - min(load.values()), 1, load)

    def test_started_fichas_keep_their_owner(self):
        text = roadmap(
            team_ficha("C-01", owner="m3", estado="[~]"),
            team_ficha("C-02", owner="m3"),
            team_ficha("C-03"),
        )
        result = split(text, team=TEAM4)
        by_id = {a["id"]: a for w in result["waves"] for a in w["fichas"]}
        self.assertEqual(by_id["C-01"]["responsable"], "m3")
        self.assertTrue(by_id["C-01"]["fijo"])
        self.assertFalse(by_id["C-02"]["fijo"])

    def test_done_fichas_are_not_split(self):
        text = roadmap(team_ficha("C-01", estado="[x]"), team_ficha("C-02", deps="C-01"))
        result = split(text, team=TEAM4)
        ids = [a["id"] for w in result["waves"] for a in w["fichas"]]
        self.assertEqual(ids, ["C-02"])

    def test_seats_without_member_are_reported_not_invented(self):
        partial = {"size": 3, "members": [{"id": "m1"}]}
        result = split(self.WIDE, team=partial)
        owners = {a["responsable"] for w in result["waves"] for a in w["fichas"]}
        self.assertLessEqual(owners, {"m1", None})
        self.assertIn(None, owners)
        self.assertTrue(any("Integrante" in w for w in result["warnings"]), result["warnings"])

    def test_ficha_without_result_is_flagged_before_approving(self):
        text = roadmap(ficha("C-01", Dependencias="ninguna"), team_ficha("C-02"))
        result = split(text, team=TEAM4)
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("C-01" in w and "Resultado" in w for w in result["warnings"]), result["warnings"])

    def test_size_can_be_given_without_team_file(self):
        result = split(self.WIDE, size=2)
        self.assertTrue(result["ok"], result)
        owners = {a["responsable"] for w in result["waves"] for a in w["fichas"]}
        self.assertEqual(owners, {None})

    def test_split_needs_a_team_size(self):
        result = split(self.WIDE)
        self.assertFalse(result["ok"])
        self.assertTrue(any("size" in e or "cantidad" in e for e in result["errors"]), result)

    def test_split_refuses_an_invalid_roadmap(self):
        result = split(roadmap(team_ficha("C-01", deps="C-02"), team_ficha("C-02", deps="C-01")), team=TEAM4)
        self.assertFalse(result["ok"])
        self.assertTrue(any("ciclo" in e for e in result["errors"]), result)


class ApplyTest(unittest.TestCase):
    """Se guarda solo el reparto aprobado y nunca toca fichas empezadas."""

    TEXT = roadmap(
        team_ficha("C-01", owner="m3", estado="[~]"),
        team_ficha("C-02"),
        team_ficha("C-03", owner="m1"),
    )

    def run_apply(self, plan, text=None, team=TEAM4):
        project = Project(text or self.TEXT, team)
        try:
            result = fichas.apply_project(str(project.dir), plan)
            after = (project.dir / "CHANGES.md").read_text(encoding="utf-8")
        finally:
            project.close()
        return result, after

    def test_writes_owner_of_pending_fichas(self):
        result, after = self.run_apply({"C-02": "m2", "C-03": "m4"})
        self.assertTrue(result["ok"], result)
        parsed = {f["id"]: f["fields"] for f in fichas.parse_changes(after)}
        self.assertEqual(parsed["C-02"]["Responsable"], "m2")
        self.assertEqual(parsed["C-03"]["Responsable"], "m4")
        self.assertEqual(sorted(result["updated"]), ["C-02", "C-03"])

    def test_started_ficha_is_never_changed(self):
        result, after = self.run_apply({"C-01": "m1", "C-02": "m2"})
        self.assertFalse(result["ok"])
        self.assertEqual(after, self.TEXT)
        self.assertTrue(any("C-01" in e for e in result["errors"]), result)

    def test_unknown_member_or_ficha_writes_nothing(self):
        for plan in ({"C-02": "m9"}, {"C-77": "m1"}):
            result, after = self.run_apply(plan)
            self.assertFalse(result["ok"], plan)
            self.assertEqual(after, self.TEXT)

    def test_ficha_without_result_is_not_assigned(self):
        text = roadmap(ficha("C-01", Dependencias="ninguna"), team_ficha("C-02"))
        result, after = self.run_apply({"C-01": "m1", "C-02": "m2"}, text=text)
        self.assertFalse(result["ok"])
        self.assertEqual(after, text)
        self.assertTrue(any("C-01" in e and "Resultado" in e for e in result["errors"]), result)

    def test_reapplying_is_idempotent(self):
        _, once = self.run_apply({"C-02": "m2"})
        result, twice = self.run_apply({"C-02": "m2"}, text=once)
        self.assertTrue(result["ok"], result)
        self.assertEqual(once, twice)
        self.assertEqual(result["updated"], [])

    def test_only_the_owner_line_changes(self):
        _, after = self.run_apply({"C-02": "m2"})
        before_lines = self.TEXT.splitlines()
        after_lines = after.splitlines()
        self.assertEqual(len(after_lines), len(before_lines) + 1)
        added = [line for line in after_lines if line not in before_lines]
        self.assertEqual(added, ["- **Responsable**: m2"])

    def test_preserves_crlf(self):
        crlf = self.TEXT.replace("\n", "\r\n")
        project = Project("", TEAM4)
        try:
            (project.dir / "CHANGES.md").write_bytes(crlf.encode("utf-8"))
            result = fichas.apply_project(str(project.dir), {"C-02": "m2"})
            raw = (project.dir / "CHANGES.md").read_bytes()
        finally:
            project.close()
        self.assertTrue(result["ok"], result)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))


class CliTest(unittest.TestCase):
    def run_cli(self, *args, text=None, team=None):
        project = Project(text or roadmap(team_ficha("C-01")), team)
        try:
            proc = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--root", str(project.dir)] + list(args),
                capture_output=True, text=True, encoding="utf-8",
            )
        finally:
            project.close()
        return proc

    def test_verify_prints_json_and_exits_zero(self):
        proc = self.run_cli("verify")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])

    def test_verify_with_errors_exits_one(self):
        proc = self.run_cli("verify", text=roadmap(team_ficha("C-01", deps="C-01")))
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_wrong_usage_exits_two_with_json(self):
        proc = self.run_cli("nada")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_split_and_apply_round_trip(self):
        project = Project(roadmap(team_ficha("C-01"), team_ficha("C-02")), TEAM4)
        try:
            base = [sys.executable, "-B", str(SCRIPT), "--root", str(project.dir)]
            proc = subprocess.run(base + ["split"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout)
            plan = {a["id"]: a["responsable"] for w in json.loads(proc.stdout)["waves"] for a in w["fichas"]}
            plan_file = project.dir / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            proc = subprocess.run(base + ["apply", "--file", str(plan_file)],
                                  capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout)
            parsed = fichas.parse_changes((project.dir / "CHANGES.md").read_text(encoding="utf-8"))
            self.assertEqual({f["fields"]["Responsable"] for f in parsed}, {"m1", "m2"})
        finally:
            project.close()

    def test_launchers_exist_and_call_the_script(self):
        sh = (ROOT / "fichas.sh").read_text(encoding="utf-8")
        bat = (ROOT / "fichas.bat").read_text(encoding="utf-8")
        self.assertIn('"$dir/fichas.py"', sh)
        self.assertIn("-B", sh)
        self.assertIn('"%~dp0fichas.py"', bat)
        self.assertIn("exit 3", sh)
        self.assertIn("exit /b 3", bat)


if __name__ == "__main__":
    unittest.main()
