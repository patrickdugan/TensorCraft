"""Read-only compatibility preflight for a private StarCraft 1.16.1 + Pluto install.

This checks static files. Only a live smoke test can establish MPQDraft,
BWAPI and Pluto compatibility or verify movement in the actual game.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
from . import mod


def filehash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_machine(path: Path) -> int | None:
    with path.open("rb") as f:
        header = f.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            return None
        pe_off = struct.unpack_from("<I", header, 0x3C)[0]
        if pe_off > 16 * 1024 * 1024:
            return None
        f.seek(pe_off)
        payload = f.read(6)
        if len(payload) != 6 or payload[:4] != b"PE\0\0":
            return None
        return struct.unpack_from("<H", payload, 4)[0]


def inspect_install(game_dir: Path, units_file: Path, original_flingy: Path,
                    staged_flingy: Path, receipt_file: Path,
                    packed_extracted_flingy: Path | None = None,
                    mpqdraft_bwapi_plugin: Path | None = None) -> dict:
    game_dir = game_dir.resolve()
    checks, failures, warnings = {}, [], []
    installed = {
        "starcraft_exe": (game_dir / "StarCraft.exe", 0x14C),
        "pluto_dll": (game_dir / "bwapi-data" / "AI" / "pluto.dll", 0x14C),
        "pluto_infer": (game_dir / "bwapi-data" / "AI" / "pluto" / "pluto_infer.exe", 0x8664),
        "pluto_weights": (game_dir / "bwapi-data" / "AI" / "pluto" / "pluto_weights.bin", None),
    }
    for name, (path, expected_machine) in installed.items():
        if not path.is_file():
            failures.append(f"missing_{name}")
            checks[name] = {"present": False}
            continue
        entry = {"present": True, "size_bytes": path.stat().st_size, "sha256": filehash(path)}
        if entry["size_bytes"] == 0:
            failures.append(f"empty_{name}")
        if expected_machine is not None:
            machine = pe_machine(path)
            entry["pe_machine"] = f"0x{machine:04x}" if machine is not None else None
            if machine != expected_machine:
                failures.append(f"wrong_pe_machine_{name}")
        checks[name] = entry
    bwapi_ini = game_dir / "bwapi-data" / "bwapi.ini"
    if not bwapi_ini.is_file():
        failures.append("missing_bwapi_ini")
    else:
        text = bwapi_ini.read_text(encoding="utf-8-sig", errors="replace")
        ai_values = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", ";")) or "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key.strip().lower() == "ai":
                ai_values.append(val.strip().replace("\\", "/").lower())
        if len(ai_values) != 1 or ai_values[0] != "bwapi-data/ai/pluto.dll":
            failures.append("bwapi_ini_ai_path_ambiguous_or_wrong")
        checks["bwapi_ini"] = {"present": True, "sha256": filehash(bwapi_ini),
                               "pluto_ai_configured": len(ai_values) == 1 and ai_values[0] == "bwapi-data/ai/pluto.dll"}
    for required_name in ("StarDat.mpq", "BrooDat.mpq", "Patch_rt.mpq"):
        found = next((p for p in game_dir.iterdir()
                      if p.is_file() and p.name.casefold() == required_name.casefold()), None) if game_dir.is_dir() else None
        if found is None:
            failures.append(f"missing_{required_name}")
        else:
            checks[required_name] = {"present": True, "size_bytes": found.stat().st_size,
                                     "sha256": filehash(found)}
    try:
        units, baseline, staged = units_file.read_bytes(), original_flingy.read_bytes(), staged_flingy.read_bytes()
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        variant = receipt["variant"]
        output, computed = mod.patch(units, baseline, variant=variant,
                                     expected_units_sha256=mod.digest(units),
                                     expected_flingy_sha256=mod.digest(baseline))
        for k in ("output_sha256", "units_sha256", "flingy_sha256", "new_speed_raw", "changed_byte_offsets"):
            if computed[k] != receipt.get(k):
                failures.append(f"receipt_{k}_mismatch")
        if staged != output:
            failures.append("staged_data_not_expected_patch")
        checks["mod"] = {"variant": variant, "original_sha256": mod.digest(baseline),
                         "staged_sha256": mod.digest(staged), "expected_sha256": computed["output_sha256"],
                         "changed_byte_offsets": computed["changed_byte_offsets"]}
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as e:
        failures.append("mod_receipt_or_files_invalid")
        checks["mod_error"] = str(e)
    if packed_extracted_flingy is None:
        warnings.append("mod_mpq_payload_not_verified")
        checks["packed_overlay"] = {"extracted": False}
    else:
        try:
            packed_hash = filehash(packed_extracted_flingy)
            staged_hash = filehash(staged_flingy)
            checks["packed_overlay"] = {"extracted": True, "sha256": packed_hash}
            if packed_hash != staged_hash:
                failures.append("mpq_extracted_mod_hash_mismatch")
        except OSError:
            failures.append("mpq_extracted_mod_missing")
    if mpqdraft_bwapi_plugin is None:
        warnings.append("bwapi_mpqdraft_plugin_not_checked")
    else:
        path = mpqdraft_bwapi_plugin
        if path.suffix.lower() != ".qdp":
            failures.append("bwapi_mpqdraft_plugin_must_be_qdp")
        if not path.is_file():
            failures.append("bwapi_mpqdraft_plugin_missing")
            checks["bwapi_mpqdraft_plugin"] = {"present": False}
        else:
            machine = pe_machine(path)
            checks["bwapi_mpqdraft_plugin"] = {
                "present": True, "sha256": filehash(path),
                "size_bytes": path.stat().st_size,
                "pe_machine": f"0x{machine:04x}" if machine is not None else None,
                "name": path.name,
            }
            if machine != 0x14C:
                failures.append("bwapi_mpqdraft_plugin_not_x86")
    warnings += ["starcraft_executable_version_unconfirmed", "cpu_avx2_unconfirmed",
                 "mpqdraft_bwapi_coexistence_unconfirmed", "pluto_v2_release_identity_unconfirmed",
                 "no_game_execution_performed"]
    return {"schema": 1, "status": "static_checks_failed" if failures else "static_checks_only",
            "checks": checks, "failures": sorted(set(failures)), "warnings": sorted(set(warnings)),
            "engine_mod_execution_proven": False}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-dir", type=Path, required=True)
    ap.add_argument("--units", type=Path, required=True)
    ap.add_argument("--original-flingy", type=Path, required=True)
    ap.add_argument("--staged-flingy", type=Path, required=True)
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--mpq-extracted-flingy", type=Path)
    ap.add_argument("--mpqdraft-bwapi-plugin", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    result = inspect_install(args.game_dir, args.units, args.original_flingy,
                             args.staged_flingy, args.receipt, args.mpq_extracted_flingy,
                             args.mpqdraft_bwapi_plugin)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2, sort_keys=True)
        out.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
