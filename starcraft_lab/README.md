# StarCraft Design Lab: Stage 0

**Status:** offline file patcher and telemetry normalizer, with synthetic tests. No actual Pluto/OpenBW matches have been run, and no live engine compatibility has been established.

## Sources and constraints

- Game engine studied: [OpenBW/openbw](https://github.com/OpenBW/openbw), `data_loading.h`, `bwenums.h`. OpenBW currently publishes no explicit license; **do not vendor its code into this MIT repository** without appropriate permission.
- Runtime bot: [tscmoo/pluto](https://github.com/tscmoo/pluto), binary-only v2.0 (`md09b_1331000`), documented for **StarCraft: Brood War 1.16.1 plus BWAPI 4.4.0**. It runs on 64-bit Windows or Wine, needs AVX2 and has per-opponent persistent opening-selection stats.
- Compatibility warning: [independent Pluto binary research](https://github.com/hwkim3330/pluto-re) indicates direct 1.16.1 memory reads/command dispatch. It covered an earlier Pluto build, so **do not assume Pluto v2.0 is an OpenBW plugin**. `telemetry.py` rejects claims of Pluto-on-OpenBW compatibility.
- Mod input is extracted `arr/units.dat` and `arr/flingy.dat` from **your legally obtained StarCraft data**. No game data, binaries, AI weights, or maps are shipped here.
- The patch is a staged `flingy.dat`, not an installable MPQ. The packaging/injection route for a 1.16.1 experiment remains to be validated in a disposable local game installation before actual Pluto runs. OpenBW's `.dat` loading and native 1.16.1 mod deployment require separate smoke tests.

## Recovered design hypotheses

1. **August 28, 2026:** Devourers rarely enter mature ZvZ air fights. Test modest mobility improvement, then separately a shorter morph and cheaper Greater Spire path. Guardians need a later redesign, not simply a speed experiment.
2. **April 25, 2026:** Scouts as mobile "Protoss Queens" to pick exposed Siege Tanks. Test seven Scouts with +1 air attack against Tank pushes, with Observer information and Goliath risk controlled. This was primarily a strategy hypothesis, not a previously agreed unit-stat buff.
3. Cross-cutting research: preserve asymmetrical cost, DPS and maneuverability, tech gates, and geographic/positional counterplay.

## First intervention

- `control`: Devourer top speed 5.00 pixels/frame, raw `1280`.
- `devourer_speed_550`: Devourer top speed 5.50 pixels/frame, raw `1408` (+10%). **This numerical buff is a new, deliberately conservative hypothesis**.
- Record a before/after byte audit; abort on mismatched SHA-256, unexpected baseline, invalid 209-entry `flingy.dat`, shared Flingy ID, or existing output file.
- We infer the Devourer's Flingy ID from byte 62 of the user-provided `units.dat` graphics table. The speed field is the 32-bit little-endian field beginning at `418 + 4 * FlingyID`, per OpenBW's loader and PyMS FlingyDAT format. A change to that one field is the only permitted patch.

## Run on locally extracted game data

```bash
python -m starcraft_lab.mod inspect --units /owned-game/arr/units.dat --flingy /owned-game/arr/flingy.dat
python -m starcraft_lab.mod patch --units /owned-game/arr/units.dat \
  --flingy /owned-game/arr/flingy.dat --variant devourer_speed_550 \
  --expect-units-sha256 <inspected-units-sha256> \
  --expect-flingy-sha256 <inspected-flingy-sha256> \
  --output /private-exp/devourer_550/flingy.dat
python -m unittest discover -s starcraft_lab/tests -v
```

The staged output includes `flingy.dat.receipt.json` with before/after hashes, numeric changes, and exact changed-byte offsets. Place the private experiment directory outside the repository.

## Evidence capture

Make a per-game manifest from `manifest.example.json`, using **actual SHA-256 values** for the engine binary, Pluto binary, map, modded data, and the **initial** per-opponent Pluto bandit stats. Never reuse the example hashes. Record the result from an identified replay or tournament referee, distinguishing `win`, `loss`, `draw`, `crash`, and `unknown`. Preserve opponent name, races, spawn side, seed and map hash. Reset Pluto's adaptive per-opponent bandit to the same starting file before each paired A/B game, using separate installations/profiles to avoid accidental mixing. Pluto's win-probability estimates are agent beliefs, not observed match results.

```bash
python -m starcraft_lab.telemetry record --manifest /private-exp/manifest.json \
  --pluto-log /private-game/bwapi-data/write/pluto_bandit_OPPONENT.txt \
  --output /private-exp/records/zvz-control-001.json
python -m starcraft_lab.telemetry analyze --records /private-exp/records
```

The collector archives Pluto's JSON event objects and hashes the source file without assuming event property names. Malformed JSON or absent Pluto logs remain flagged; games without verified outcome evidence are excluded from the verified win-rate denominator. Paired analysis only compares the same map, side, seed, opponent, agent, version and initial bandit configuration. Zero matches produces a `null` win rate, never a fabricated zero.

## First experiment protocol and acceptance gates

- **Stage 0:** source inspection, data mapping, input hash guard, byte-level patch proof, synthetic fixture tests. This repository implements that stage.
- **Stage 1:** disposable installation smoke check: confirm Devourer movement in OpenBW with original data and patched data, and separately in StarCraft 1.16.1 with a safely packaged `.dat` overlay. Check actual pixel displacement per frame and that all unrelated unit statistics are unchanged.
- **Stage 2:** original-game Pluto v2.0 baseline against fixed bots, maps and openings. Confirm game outcomes with replays/referee, normalize telemetry. Use 30 matched A/B pairs as a *starting exploratory sample*, with mirrored spawns. Do not interpret early results as a definitive balance verdict.
- **Stage 3:** modded Pluto runs only after verifying the native game loads the patch and the agent receives consistent observations. Repeat with new opponents/maps; isolate one intervention at a time. Extract Devourer build incidence, first morph timing, engagements, survival time, tech timing, and gas/mineral opportunity cost from replays or a verified recorder. The present telemetry module does not infer those metrics.
- **Stage 4:** adapt the same pipeline to Scout tank-assassination scenarios, Guardian redesign and Greater Spire tempo, with fresh baselines.

**Integrity gates:** Pluto crashes are never assigned losses; unlabeled/manual outcomes are kept out of verified results; unexpected file layouts and shared Flingy references fail closed. A race's changed win rate alone cannot establish which mechanic caused it if the original openings, map positions, or opponent behavior differ.
