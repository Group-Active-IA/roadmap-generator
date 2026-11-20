"""Verificador y reparto de fichas de CHANGES.md para el modo equipo ([EQ] Fase 2).

Solo biblioteca estandar, Python 3.8+, sin red ni git. Uso:
    fichas.py --root <proyecto> verify
    fichas.py --root <proyecto> split [--size N]
    fichas.py --root <proyecto> apply --file <reparto.json>
Imprime siempre un JSON por stdout. Codigos: 0 ok, 1 invalido, 2 uso.

Las reglas de cada campo de ficha viven en `team.py` de la skill
`active-delivery` (contrato de R-06). Este script lo busca en la carpeta
hermana y lo reutiliza; si no esta instalada (OpenCode, Antigravity), hace los
chequeos entre fichas y avisa que los campos no se validaron.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

CHANGES_FILE = "CHANGES.md"
TEAM_PATH = os.path.join(".active-stack", "team.json")
DELIVERY_SKILL = "active-delivery"

TEAM_FIELDS = ("Resultado", "Depende de", "Zona", "Requiere", "Modo", "Responsable")
DEPENDENCY_FIELDS = ("Depende de", "Dependencias")
NO_DEPENDENCIES = ("ninguna", "—", "-", "")
MAX_OPEN_PRS = 2

HEADING_RE = re.compile(r"^###\s+\[([^\]]+)\]")
SECTION_RE = re.compile(r"^(#{1,3}\s|---\s*$)")
FIELD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s*:\s*(.*)$")
OPEN_PR_RE = re.compile(r"^#[0-9]+@[0-9a-fA-F]{7,40}$")
PR_RE = re.compile(r"^#[0-9]+(?:@[0-9a-fA-F]{7,40})?$")

FIELD_VALIDATION_MISSING = (
    "validacion de campos no disponible en este agente: falta la skill %s "
    "(se instala en Claude y Codex). Se revisaron ciclos, zonas, cadenas de PR, "
    "resultado y responsable" % DELIVERY_SKILL
)


# ---------------------------------------------------------------- lectura

def parse_changes(text):
    """Devuelve las fichas de CHANGES.md: id, campos, estado y lineas."""
    lines = text.splitlines()
    result = []
    current = None
    for index, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if heading:
            current = {"id": heading.group(1).strip(), "fields": {}, "field_lines": {},
                       "start": index, "end": index + 1}
            result.append(current)
            continue
        if current is None:
            continue
        if SECTION_RE.match(line):
            current = None
            continue
        current["end"] = index + 1
        field = FIELD_RE.match(line)
        if field and field.group(1).strip() not in current["fields"]:
            name = field.group(1).strip()
            current["fields"][name] = field.group(2).strip()
            current["field_lines"][name] = index
    for item in result:
        item["state"] = _state(item["fields"].get("Estado", ""))
    return result


def _state(value):
    if "[ ]" in value:
        return "pending"
    if "[x]" in value.lower():
        return "done"
    return "started"


def _items(value):
    text = value.replace("`", "").strip()
    if text.lower() in NO_DEPENDENCIES:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _dependencies(fields):
    for name in DEPENDENCY_FIELDS:
        if name in fields:
            return _items(fields[name])
    return []


def _zones(value):
    zones = []
    for item in _items(value):
        zone = item.replace("\\", "/")
        while zone.startswith("./"):
            zone = zone[2:]
        for suffix in ("/**", "/*"):
            if zone.endswith(suffix):
                zone = zone[: -len(suffix)]
        zone = zone.rstrip("/")
        if zone:
            zones.append(zone)
    return zones


def _overlaps(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _read_text(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def _load_team(root):
    """Devuelve (team_doc o None, error o None)."""
    path = os.path.join(root, TEAM_PATH)
    if not os.path.isfile(path):
        return None, None
    try:
        doc = json.loads(_read_text(path))
    except (OSError, ValueError) as exc:
        return None, "%s: no se pudo leer (%s)" % (TEAM_PATH.replace(os.sep, "/"), exc)
    if not isinstance(doc, dict):
        return None, "%s: la raiz debe ser un objeto JSON" % TEAM_PATH.replace(os.sep, "/")
    return doc, None


def _member_ids(team):
    members = team.get("members") if isinstance(team, dict) else None
    if not isinstance(members, list):
        return []
    return [m["id"] for m in members if isinstance(m, dict) and isinstance(m.get("id"), str)]


# ---------------------------------------------------------------- validador de campos

def load_field_validator(skill_dir):
    """Busca `validate_ficha` en ../active-delivery/team.py. None si no esta o no carga."""
    path = os.path.join(os.path.dirname(os.path.abspath(skill_dir)), DELIVERY_SKILL, "team.py")
    if not os.path.isfile(path):
        return None
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("active_delivery_team", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:  # un team.py roto no debe frenar la verificacion
        return None
    finally:
        sys.dont_write_bytecode = previous
    validator = getattr(module, "validate_ficha", None)
    return validator if callable(validator) else None


def _field_payload(item):
    payload = {"id": item["id"]}
    for name, value in item["fields"].items():
        payload[name] = value.replace("`", "").strip() if name in DEPENDENCY_FIELDS else value
    return payload


# ---------------------------------------------------------------- verificacion

def _graph_errors(fichas, known):
    errors = []
    graph = {}
    for item in fichas:
        deps = []
        for dep in _dependencies(item["fields"]):
            if PR_RE.match(dep):
                continue
            if dep not in known:
                errors.append("%s: depende de %s, que no existe en %s" % (item["id"], dep, CHANGES_FILE))
                continue
            deps.append(dep)
        graph[item["id"]] = deps
    return graph, errors


def _cycles(graph):
    """Devuelve cada ciclo una vez, como lista de ids."""
    color = {node: 0 for node in graph}
    stack = []
    found = []
    seen = set()

    def visit(node):
        color[node] = 1
        stack.append(node)
        for dep in graph[node]:
            if color[dep] == 1:
                cycle = stack[stack.index(dep):]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    found.append(cycle + [dep])
            elif color[dep] == 0:
                visit(dep)
        stack.pop()
        color[node] = 2

    for node in graph:
        if color[node] == 0:
            visit(node)
    return found


def _reachable(graph):
    reach = {}

    def walk(node, acc):
        for dep in graph[node]:
            if dep not in acc:
                acc.add(dep)
                walk(dep, acc)
        return acc

    for node in graph:
        reach[node] = walk(node, set())
    return reach


def _open_pr_depth(graph, by_id):
    """PR abiertos (#N@SHA) que hay debajo de cada ficha, siguiendo fichas sin terminar."""
    memo = {}

    def depth(node):
        if node in memo:
            return memo[node]
        memo[node] = 0
        own = sum(1 for dep in _dependencies(by_id[node]["fields"]) if OPEN_PR_RE.match(dep))
        below = 0
        for dep in graph[node]:
            if by_id[dep]["state"] != "done":
                below = max(below, depth(dep))
        memo[node] = own + below
        return memo[node]

    return {node: depth(node) for node in graph}


def _verify(fichas, team, team_error, validator):
    errors = []
    warnings = []
    if team_error:
        errors.append(team_error)

    ids = [item["id"] for item in fichas]
    known = set()
    for fid in ids:
        if fid in known:
            errors.append("%s: el id esta repetido; cada ficha necesita un id unico" % fid)
        known.add(fid)
    by_id = {}
    for item in fichas:
        by_id.setdefault(item["id"], item)
    unique = [item for item in fichas if by_id[item["id"]] is item]

    member_ids = _member_ids(team) if team is not None else None
    for item in unique:
        fields = item["fields"]
        is_team_ficha = any(name in fields for name in TEAM_FIELDS if name != "Depende de") or "Depende de" in fields
        result = fields.get("Resultado")
        if result is None:
            if is_team_ficha:
                errors.append("%s: falta Resultado; sin resultado verificable no hay ficha valida" % item["id"])
            else:
                warnings.append("%s: ficha sin los campos del modo equipo (falta Resultado)" % item["id"])
        elif not result.strip():
            errors.append("%s: Resultado esta vacio" % item["id"])

        owner = fields.get("Responsable")
        if owner:
            if member_ids is None:
                if not team_error:
                    warnings.append("%s: Responsable %s sin %s para comprobarlo"
                                    % (item["id"], owner, TEAM_PATH.replace(os.sep, "/")))
            elif owner not in member_ids:
                errors.append("%s: Responsable %s no es un miembro del equipo (miembros: %s)"
                              % (item["id"], owner, ", ".join(member_ids) or "ninguno"))

        if validator is not None:
            try:
                checked = validator(_field_payload(item), None)
            except Exception as exc:
                errors.append("%s: el validador de campos fallo (%s)" % (item["id"], exc))
                continue
            errors.extend("%s: %s" % (item["id"], e) for e in checked.get("errors", []))

    if validator is None:
        warnings.append(FIELD_VALIDATION_MISSING)

    graph, graph_errors = _graph_errors(unique, set(by_id))
    errors.extend(graph_errors)
    cycles = _cycles(graph)
    for cycle in cycles:
        errors.append("ciclo de dependencias: %s" % " -> ".join(cycle))

    if not cycles:
        depth = _open_pr_depth(graph, by_id)
        for item in unique:
            if item["state"] != "done" and depth[item["id"]] > MAX_OPEN_PRS:
                errors.append("%s: queda encima de %d PR abiertos encadenados (maximo %d); "
                              "extrae la base comun a un PR chico"
                              % (item["id"], depth[item["id"]], MAX_OPEN_PRS))

        reach = _reachable(graph)
        active = [item for item in unique if item["state"] != "done" and item["fields"].get("Zona")]
        for i, first in enumerate(active):
            for second in active[i + 1:]:
                if second["id"] in reach[first["id"]] or first["id"] in reach[second["id"]]:
                    continue
                shared = sorted({a if len(a) <= len(b) else b
                                 for a in _zones(first["fields"]["Zona"])
                                 for b in _zones(second["fields"]["Zona"]) if _overlaps(a, b)})
                if shared:
                    warnings.append("zona compartida entre %s y %s (%s): pueden ir en paralelo; "
                                    "revisar, no es prueba de conflicto"
                                    % (first["id"], second["id"], ", ".join(shared)))
    return errors, warnings, graph, cycles


def _load(root):
    path = os.path.join(root, CHANGES_FILE)
    if not os.path.isfile(path):
        return None, "%s: no existe en %s" % (CHANGES_FILE, root)
    try:
        return _read_text(path), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, "%s: no se pudo leer (%s)" % (CHANGES_FILE, exc)


def verify_project(root, validator=None):
    text, error = _load(root)
    if error:
        return {"ok": False, "errors": [error], "warnings": [], "field_validation": validator is not None}
    fichas = parse_changes(text)
    team, team_error = _load_team(root)
    errors, warnings, _, _ = _verify(fichas, team, team_error, validator)
    return {"ok": not errors, "fichas": len(fichas), "errors": errors, "warnings": warnings,
            "field_validation": validator is not None}


# ---------------------------------------------------------------- reparto

def _waves(graph, by_id):
    wave = {}

    def level(node):
        if node not in wave:
            pending = [dep for dep in graph[node] if by_id[dep]["state"] != "done"]
            wave[node] = 1 + max((level(dep) for dep in pending), default=0)
        return wave[node]

    for node in graph:
        level(node)
    return wave


def split_project(root, size=None):
    """Propone un reparto editable de las fichas sin terminar. No escribe nada."""
    text, error = _load(root)
    if error:
        return {"ok": False, "errors": [error], "warnings": []}
    fichas = parse_changes(text)
    team, team_error = _load_team(root)
    errors, warnings, graph, cycles = _verify(fichas, team, team_error, None)
    warnings = [w for w in warnings if w != FIELD_VALIDATION_MISSING]
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    if size is None and team is not None:
        size = team.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 2:
        return {"ok": False, "warnings": warnings,
                "errors": ["falta la cantidad de integrantes: usa --size N (N >= 2) o %s con size"
                           % TEAM_PATH.replace(os.sep, "/")]}

    members = _member_ids(team) if team is not None else []
    seats = members[:size] + [None] * (size - len(members[:size]))
    if None in seats:
        warnings.append("%d de %d lugares sin integrante (Integrante %s): sus fichas quedan sin "
                        "Responsable hasta completar el equipo"
                        % (seats.count(None), size,
                           ", ".join(str(i + 1) for i, s in enumerate(seats) if s is None)))

    by_id = {item["id"]: item for item in fichas}
    wave_of = _waves(graph, by_id)
    open_items = [item for item in fichas if item["state"] != "done"]
    load = {index: 0 for index in range(size)}
    for item in open_items:
        owner = item["fields"].get("Responsable")
        if item["state"] == "started" and owner in seats:
            load[seats.index(owner)] += 1

    waves = []
    for number in sorted({wave_of[item["id"]] for item in open_items}):
        in_wave = [item for item in open_items if wave_of[item["id"]] == number]
        busy = set()
        entries = []
        for item in in_wave:
            owner = item["fields"].get("Responsable")
            if item["state"] == "started":
                entries.append({"id": item["id"], "responsable": owner or None, "fijo": True})
                if owner in seats:
                    busy.add(seats.index(owner))
        for item in in_wave:
            if item["state"] == "started":
                continue
            free = [i for i in range(size) if i not in busy] or list(range(size))
            seat = min(free, key=lambda i: (load[i], i))
            load[seat] += 1
            busy.add(seat)
            entries.append({"id": item["id"], "responsable": seats[seat], "fijo": False})
        idle = [seats[i] if seats[i] else "Integrante %d" % (i + 1) for i in range(size) if i not in busy]
        order = {item["id"]: index for index, item in enumerate(in_wave)}
        entries.sort(key=lambda entry: order[entry["id"]])
        waves.append({"ola": number, "fichas": entries, "sin_ficha": idle})

    parallelism = max((len(w["fichas"]) for w in waves), default=0)
    return {"ok": True, "size": size, "parallelism": parallelism, "waves": waves, "warnings": warnings,
            "errors": [],
            "note": "propuesta editable: sin_ficha revisa PR o adelanta la ola siguiente; "
                    "se guarda solo lo aprobado con apply"}


def apply_project(root, plan):
    """Escribe Responsable en las fichas pendientes del reparto aprobado. Todo o nada."""
    if not isinstance(plan, dict) or not all(isinstance(k, str) for k in plan):
        return {"ok": False, "errors": ["el reparto debe ser un objeto {\"ID\": \"miembro\"}"], "updated": []}
    text, error = _load(root)
    if error:
        return {"ok": False, "errors": [error], "updated": []}
    team, team_error = _load_team(root)
    if team_error:
        return {"ok": False, "errors": [team_error], "updated": []}
    if team is None:
        return {"ok": False, "errors": ["falta %s: el Responsable es un id de integrante"
                                        % TEAM_PATH.replace(os.sep, "/")], "updated": []}
    members = _member_ids(team)
    fichas = {item["id"]: item for item in parse_changes(text)}

    errors = []
    for fid, owner in plan.items():
        item = fichas.get(fid)
        if item is None:
            errors.append("%s: no existe en %s" % (fid, CHANGES_FILE))
        elif item["state"] != "pending":
            errors.append("%s: ya esta empezada o terminada; su Responsable no se cambia" % fid)
        elif not (item["fields"].get("Resultado") or "").strip():
            errors.append("%s: falta Resultado; completalo antes de asignarle Responsable" % fid)
        elif owner not in members:
            errors.append("%s: %r no es un miembro del equipo (miembros: %s)"
                          % (fid, owner, ", ".join(members) or "ninguno"))
    if errors:
        return {"ok": False, "errors": errors, "updated": []}

    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()
    updated = []
    edits = []
    for fid, owner in plan.items():
        item = fichas[fid]
        line = "- **Responsable**: %s" % owner
        if "Responsable" in item["field_lines"]:
            index = item["field_lines"]["Responsable"]
            if lines[index] != line:
                edits.append((index, "replace", line))
                updated.append(fid)
        else:
            anchor = max(item["field_lines"].values()) if item["field_lines"] else item["start"]
            for name in ("Modo", "Requiere", "Zona", "Depende de", "Dependencias", "Resultado", "Estado"):
                if name in item["field_lines"]:
                    anchor = item["field_lines"][name]
                    break
            edits.append((anchor, "insert", line))
            updated.append(fid)
    for index, kind, line in sorted(edits, key=lambda e: e[0], reverse=True):
        if kind == "replace":
            lines[index] = line
        else:
            lines.insert(index + 1, line)
    if updated:
        body = newline.join(lines) + (newline if trailing else "")
        with open(os.path.join(root, CHANGES_FILE), "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
    return {"ok": True, "errors": [], "updated": sorted(updated)}


# ---------------------------------------------------------------- CLI

class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        _emit({"ok": False, "errors": ["uso incorrecto: %s" % message]})
        sys.stderr.write(self.format_usage())
        sys.exit(2)


def _emit(result):
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def _build_parser():
    parser = _JsonArgumentParser(prog="fichas.py", description="Verifica y reparte las fichas de CHANGES.md.")
    parser.add_argument("--root", default=".", help="raiz del proyecto (por defecto, la carpeta actual)")
    sub = parser.add_subparsers(dest="command", parser_class=_JsonArgumentParser)
    sub.required = True
    sub.add_parser("verify", help="verifica CHANGES.md sin red")
    split = sub.add_parser("split", help="propone un reparto editable (no escribe)")
    split.add_argument("--size", type=int, help="cantidad de integrantes si no hay team.json")
    apply = sub.add_parser("apply", help="escribe el Responsable del reparto aprobado")
    apply.add_argument("--file", required=True, help='JSON {"ID": "miembro"} aprobado por la persona')
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    args = _build_parser().parse_args(argv)
    if args.command == "verify":
        validator = load_field_validator(os.path.dirname(os.path.abspath(__file__)))
        result = verify_project(args.root, validator=validator)
    elif args.command == "split":
        result = split_project(args.root, size=args.size)
    else:
        try:
            plan = json.loads(_read_text(args.file))
        except (OSError, ValueError) as exc:
            result = {"ok": False, "errors": ["%s: no se pudo leer (%s)" % (args.file, exc)], "updated": []}
        else:
            result = apply_project(args.root, plan)
    _emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
