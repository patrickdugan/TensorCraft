"""Fail-closed schema for a parallel StarCraft 1.16.1/BWAPI/Pluto farm.

Private owned game binaries live *outside* this repository. The farm is a
controller for pre-provisioned, isolated worker installations; it does not
solve native MPQDraft/bwheadless integration by assuming a command exists.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import string

from starcraft_lab.batch import PINNED_FILES

HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
ALLOWED_TEMPLATE_FIELDS = frozenset({
    "run_id", "pair_id", "variant", "map_path", "mod_mpq",
    "replay_path", "job_dir", "game_dir", "wineprefix",
    "display", "slot_id", "seed_label", "port_base",
})
REQUIRED_LAUNCH_FIELDS = frozenset({
    "run_id", "map_path", "mod_mpq", "replay_path",
})
FPS_DEFAULT = 30
RASTER = {"width": 640, "height": 480}


class FarmError(ValueError):
    """Configuration, isolation, or artifact-integrity failure."""


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safe_id(value: object, where: str) -> str:
    if not isinstance(value, str) or IDENT.fullmatch(value) is None:
        raise FarmError(f"{where}: expected safe ASCII ID")
    return value


def canonical_dir(value: object, where: str, *, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise FarmError(f"{where}: requires absolute directory")
    p = Path(value).resolve()
    if must_exist and not p.is_dir():
        raise FarmError(f"{where}: directory missing: {p}")
    return p


def canonical_file(value: object, where: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise FarmError(f"{where}: requires absolute file")
    p = Path(value).resolve()
    if not p.is_file():
        raise FarmError(f"{where}: file missing: {p}")
    return p


def contained(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_command(argv: object) -> frozenset[str]:
    if not isinstance(argv, list) or not (1 <= len(argv) <= 40):
        raise FarmError("launcher.argv must contain 1..40 argument strings")
    used: set[str] = set()
    fmt = string.Formatter()
    for item in argv:
        if not isinstance(item, str) or not item or len(item) > 4096:
            raise FarmError("invalid launcher argument")
        try:
            tokens = list(fmt.parse(item))
        except ValueError as ex:
            raise FarmError("malformed launcher argv template") from ex
        for _, name, formatting, conversion in tokens:
            if name is not None:
                if name not in ALLOWED_TEMPLATE_FIELDS or formatting or conversion:
                    raise FarmError(f"invalid launcher template parameter: {name}")
                used.add(name)
    if not REQUIRED_LAUNCH_FIELDS <= used:
        raise FarmError(f"launcher must consume {sorted(REQUIRED_LAUNCH_FIELDS-used)}")
    return frozenset(used)


def validate_plan(plan: dict) -> tuple[list[list[dict]], set[str]]:
    if not isinstance(plan, dict) or plan.get("schema") != 1:
        raise FarmError("unsupported experiment plan")
    games = plan.get("games")
    if not isinstance(games, list) or not games or len(games) > 4000:
        raise FarmError("experiment plan needs 1..4000 game entries")
    pairs: dict[str, list[dict]] = defaultdict(list)
    all_ids = set()
    variants = set()
    expected_bandit = plan.get("source_bandit_sha256")
    if not isinstance(expected_bandit, str) or HEX.fullmatch(expected_bandit) is None:
        raise FarmError("plan missing initial bandit SHA-256")
    for game in games:
        if not isinstance(game, dict) or not isinstance(game.get("manifest"), dict):
            raise FarmError("malformed game entry")
        m = game["manifest"]
        rid = safe_id(m.get("run_id"), "run_id")
        pid = safe_id(m.get("pair_id"), "pair_id")
        variant = safe_id(m.get("variant"), "variant")
        if rid in all_ids:
            raise FarmError("duplicate run_id")
        all_ids.add(rid)
        variants.add(variant)
        if m.get("engine") != "starcraft_1161" or m.get("agent") != "pluto":
            raise FarmError("only classic Brood War 1.16.1 + Pluto is accepted")
        if m.get("bandit_initial_sha256") != expected_bandit:
            raise FarmError("inconsistent opening-bandit hashes")
        for name in ("engine_sha256", "agent_binary_sha256", "infer_sha256",
                     "weights_sha256", "bwapi_ini_sha256", "map_sha256"):
            value = m.get(name)
            if not isinstance(value, str) or HEX.fullmatch(value) is None:
                raise FarmError(f"malformed expected {name}")
        if type(m.get("assignment_index")) is not int or m["assignment_index"] < 1:
            raise FarmError("invalid assignment index")
        if type(m.get("seed")) is not int or not 0 <= m["seed"] < 2**32:
            raise FarmError("invalid requested seed label")
        canonical_file(game.get("map_file"), "planned map")
        pairs[pid].append(game)
    if len(set(m["assignment_index"] for m in [g["manifest"] for g in games])) != len(games):
        raise FarmError("assignment indices must be unique")
    result = sorted(pairs.values(), key=lambda items: min(x["manifest"]["assignment_index"] for x in items))
    for pair in result:
        if len(pair) != 2 or len({x["manifest"]["variant"] for x in pair}) != 2:
            raise FarmError("each block must contain exactly two distinct A/B arms")
        pair.sort(key=lambda g: g["manifest"]["assignment_index"])
        a, b = (g["manifest"] for g in pair)
        if (a["map_sha256"] != b["map_sha256"] or
            a["seed"] != b["seed"] or a.get("spawn") != b.get("spawn")):
            raise FarmError("A/B block has conflicting planned conditions")
    return result, variants


def validate_runtime(runtime: dict, plan: dict, work_root: Path) -> dict:
    if not isinstance(runtime, dict) or runtime.get("schema") != 1:
        raise FarmError("runtime configuration schema must be 1")
    pairs, variants = validate_plan(plan)
    if not isinstance(runtime.get("mods"), dict) or set(runtime["mods"]) != variants:
        raise FarmError("mod archive declarations must match planned arms exactly")
    mods = {}
    for variant in variants:
        declaration = runtime["mods"][variant]
        if not isinstance(declaration, dict):
            raise FarmError(f"invalid mod declaration for {variant}")
        path = canonical_file(declaration.get("mpq"), f"{variant}.mpq")
        expected = declaration.get("sha256")
        if not isinstance(expected, str) or HEX.fullmatch(expected) is None:
            raise FarmError("missing mod MPQ SHA-256")
        if hash_file(path) != expected:
            raise FarmError(f"{variant}: MPQ SHA-256 mismatch")
        mods[variant] = {"mpq": str(path), "sha256": expected}
    if len({x["sha256"] for x in mods.values()}) != len(mods):
        raise FarmError("different mod variants must have different MPQ hashes")

    expected = runtime.get("capacity")
    if not isinstance(expected, dict):
        raise FarmError("missing explicit CPU/RAM capacity")
    for field in ("max_parallel", "cpu_budget_threads", "threads_per_match",
                  "ram_budget_mb", "ram_per_match_mb"):
        if type(expected.get(field)) is not int or expected[field] < 1:
            raise FarmError(f"invalid capacity.{field}")
    count = expected["max_parallel"]
    if count > 16 or count * expected["threads_per_match"] > expected["cpu_budget_threads"]:
        raise FarmError("parallelism exceeds pinned CPU budget")
    if count * expected["ram_per_match_mb"] > expected["ram_budget_mb"]:
        raise FarmError("parallelism exceeds pinned RAM budget")

    launcher = runtime.get("launcher")
    if not isinstance(launcher, dict):
        raise FarmError("missing launcher definition")
    validate_command(launcher.get("argv"))
    timeout = launcher.get("timeout_seconds")
    if type(timeout) is not int or not 30 <= timeout <= 86400:
        raise FarmError("launcher timeout_seconds outside 30..86400")

    declared = runtime.get("slots")
    if not isinstance(declared, list) or len(declared) < count or len(declared) > 16:
        raise FarmError("need at least max_parallel private worker slots")
    slots = []
    seen_ids, seen_game, seen_prefix, seen_display, seen_port = set(), set(), set(), set(), set()
    originals = [canonical_dir(g["game_dir"], "planned original game")
                 for block in pairs for g in block]
    for entry in declared:
        if not isinstance(entry, dict):
            raise FarmError("worker slot must be a dictionary")
        ident = safe_id(entry.get("id"), "slot.id")
        game_dir = canonical_dir(entry.get("game_dir"), "slot.game_dir")
        prefix = canonical_dir(entry.get("wineprefix"), "slot.wineprefix")
        display = entry.get("display")
        port = entry.get("port_base")
        if not isinstance(display, str) or re.fullmatch(r":[1-9][0-9]{0,3}", display) is None:
            raise FarmError("slot DISPLAY must be unique X11 :N display")
        if type(port) is not int or port < 10000 or port > 60000:
            raise FarmError("slot port_base must be in 10000..60000")
        if (ident in seen_ids or game_dir in seen_game or prefix in seen_prefix
            or display in seen_display or port in seen_port):
            raise FarmError("worker slots share ID, game install, Wine prefix, DISPLAY or port")
        if (contained(work_root, game_dir) or contained(game_dir, work_root)
            or any(game_dir == original for original in originals)):
            raise FarmError("worker install must not overwrite an original or experiment output")
        paths = entry.get("bandit_paths")
        if not isinstance(paths, list) or not (1 <= len(paths) <= 2):
            raise FarmError("slot bandit_paths must specify private read/write files")
        bandit_paths = []
        for raw in paths:
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise FarmError("bandit path must be absolute")
            bandit = Path(raw).resolve()
            if not contained(bandit, game_dir) or bandit == game_dir:
                raise FarmError("bandit path must live inside the disposable worker install")
            if (not bandit.name.startswith("pluto_bandit_") or
                not bandit.name.endswith(".txt") or
                bandit.parent not in (game_dir/"bwapi-data"/"read",
                                     game_dir/"bwapi-data"/"write")):
                raise FarmError("bandit write targets must be Pluto stats in bwapi-data/read or write")
            bandit_paths.append(str(bandit))
        if len(set(bandit_paths)) != len(bandit_paths):
            raise FarmError("duplicated bandit path")
        observed_pins = {key: hash_file(canonical_file(str(game_dir / relative), key))
                         for key, relative in PINNED_FILES.items()}
        for block in pairs:
            for g in block:
                m = g["manifest"]
                for key in PINNED_FILES:
                    if observed_pins[key] != m[key]:
                        raise FarmError(f"{ident}: private game install differs in {key}")
        slots.append({
            "id": ident, "game_dir": str(game_dir), "wineprefix": str(prefix),
            "display": display, "port_base": port,
            "bandit_paths": bandit_paths, "pins": observed_pins
        })
        seen_ids.add(ident); seen_game.add(game_dir); seen_prefix.add(prefix)
        seen_display.add(display); seen_port.add(port)
    if any(contained(work_root, Path(x["game_dir"])) for x in slots):
        raise FarmError("output root cannot be inside an installation")
    return {"pairs": len(pairs), "games": sum(len(x) for x in pairs),
            "variants": sorted(variants), "mods": mods, "slots": slots,
            "capacity": expected, "launcher": launcher}


def substitute_command(template: list[str], values: dict[str, str]) -> list[str]:
    validate_command(template)
    expanded = [item.format_map(values) for item in template]
    if not expanded or any(not item or len(item) > 8192 for item in expanded):
        raise FarmError("bad command expansion")
    return expanded
