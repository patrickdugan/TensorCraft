# StarCraft Design Lab: Stage 0

**Status:** offline data patcher, installation static preflight, replay-core order probe, and telemetry summarizer with synthetic tests. No actual Pluto/OpenBW matches or live movement checks have been run.

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

Make a per-game manifest from `manifest.example.json`, using **actual SHA-256 values** for the engine binary, Pluto binary, map, modded data, and the **initial** per-opponent Pluto bandit stats. Never reuse the example hashes. Record the result from an identified replay or tournament referee, put its SHA-256 in `result_evidence_sha256`, and supply the original evidence file when recording. Distinguish `win`, `loss`, `draw`, `crash`, and `unknown`. Preserve opponent name, races, spawn side, seed and map hash. Reset Pluto's adaptive per-opponent bandit to the same starting file before each paired A/B game, using separate installations/profiles to avoid accidental mixing. Pluto's win-probability estimates are agent beliefs, not observed match results.

```bash
python -m starcraft_lab.telemetry record --manifest /private-exp/manifest.json \
  --pluto-log /private-game/bwapi-data/write/pluto_bandit_OPPONENT.txt \
  --result-evidence-file /private-exp/replays/zvz-control-001.rep \
  --output /private-exp/records/zvz-control-001.json
python -m starcraft_lab.telemetry analyze --records /private-exp/records
```

The collector archives Pluto's JSON event objects and hashes the source file without assuming event property names. **Hash agreement proves only that the supplied file matches the manifest; it does not authenticate the replay, game outcome, or agent identity.** These results are labelled artifact-backed reported outcomes pending an independent replay/referee result checker. Malformed JSON, absent Pluto logs or missing/mismatched outcome evidence remain flagged; games without an evidence-file SHA-256 match are excluded from the artifact-backed reported win-rate denominator. Paired analysis requires different mod hashes and compares the same map, side, seed, opponent, agent, version and initial bandit configuration. Zero matches produces a `null` win rate, never a fabricated zero.

## Replay analysis integration option

[broodwar-live/bw-engine](https://github.com/broodwar-live/bw-engine) now provides the MIT-licensed **replay-core** crate for legacy 1.16.1 replay parsing, player actions, build orders, and APM. This is a useful route to the first genuine behavior metrics from Pluto vs control/mod matches. Its **crates/bw-engine simulation is experimental/frozen and excluded from that MIT grant** because it derives in part from the unlicensed OpenBW code. The project explicitly distinguishes those components in LICENSE and NOTICE. Do not link the unlicensed simulation crate into this package. Outcome verification against replay/referee content must precede treating match-result declarations as independently confirmed results.

The original Devourer 5.00 px/frame speed is independently documented by [Liquipedia's unit movement table](https://liquipedia.net/starcraft/Unit_Movement_Speed). The patch still rejects unexpected actual data values; published tables are used as a cross-check, not a substitute for inspecting owned data.

## Stage 1 support: private mod launch and replay probe

The precise, reversible packing and launcher protocol is in [STAGE1_SMOKE.md](STAGE1_SMOKE.md). Use it before attempting match runs. A live game is required to confirm MPQ loading, unchanged injection behavior, and Devourer speed; static file hashes alone cannot do that.

The Python static audit accepts an owned 1.16.1 installation and extracted data. It checks that the staged patch matches the receipt, that the optional MPQ-extracted payload matches, and that Pluto's DLL and inference executable have the expected PE architectures. It reports unproven gameplay/version/CPU requirements explicitly. Run it with python -m starcraft_lab.preflight --help.

For actual 1.16.1 replays, use the pinned MIT-only Rust replay-core probe:

~~~bash
cargo run --release --manifest-path starcraft_lab/replay_probe/Cargo.toml -- /private-exp/replays/game.rep /private-exp/parsed/game.json
python -m starcraft_lab.telemetry record --manifest /private-exp/manifest.json --pluto-log /private-exp/pluto_bandit_OPPONENT.txt --result-evidence-file /private-exp/replays/game.rep --replay-report /private-exp/parsed/game.json --output /private-exp/records/game.json
python -m starcraft_lab.telemetry analyze --records /private-exp/records
~~~

Set agent_player_id in the manifest to the appropriate replay-header ID. The collector rejects mismatched replay hashes, inconsistent first-leaver outcomes, incorrect race attribution and incomplete command decoding; it aggregates unit **order** incidence and timing by variant. These are observations of commands and model-estimated production, not combat snapshots. For full details consult the smoke protocol.

## Stage 2: balanced Pluto A/B batch planner

The [batch experiment guide](BATCH.md) provides the exact host workflow and a [Windows example specification](batch.example.json). Generate 30 paired trials (60 game manifests) with one command:

~~~bash
python -m starcraft_lab.batch plan --spec /private-exp/experiment.json --output /private-exp/runs/devourer_v1
~~~

The planner freezes initial Pluto per-opponent bandit bytes, hashes two isolated game installations and all inference/model binaries, and balances control/treatment order and requested A/B spawn positions. It never launches or changes the game installation. A run's requested map RNG seed and spawn are labels pending a live launcher verification.

For evidence-backed records, summarize paired reported wins and Devourer order adoption:

~~~bash
python -m starcraft_lab.batch analyze --plan /private-exp/runs/devourer_v1/plan.json --records /private-exp/runs/devourer_v1/records
~~~

The paired exact sign test and seeded exploratory bootstrap interval are descriptive until the native MPQDraft/BWAPI+Pluto pathway and real game conditions are independently verified. BWAPI's own MPQDraft launcher supports selecting the archive and BWAPI injector plugin together; see [STAGE1_SMOKE.md](STAGE1_SMOKE.md). Pluto's native 1.16.1 dependency remains the reason experiments currently use the original executable.

## First experiment protocol and acceptance gates

- **Stage 0:** source inspection, data mapping, input hash guard, byte-level patch proof, synthetic fixture tests. This repository implements that stage.
- **Stage 1:** static preflight and replay tooling implemented; live disposable installation smoke check: confirm Devourer movement in OpenBW with original data and patched data, and separately in StarCraft 1.16.1 with a safely packaged `.dat` overlay. Check actual pixel displacement per frame and that all unrelated unit statistics are unchanged.
- **Stage 2:** original-game Pluto v2.0 baseline against fixed bots, maps and openings. Confirm game outcomes with replays/referee, normalize telemetry. Use 30 matched A/B pairs as a *starting exploratory sample*, with mirrored spawns. Do not interpret early results as a definitive balance verdict.
- **Stage 3:** modded Pluto runs only after verifying the native game loads the patch and the agent receives consistent observations. Repeat with new opponents/maps; isolate one intervention at a time. Extract Devourer build incidence, first morph timing, engagements, survival time, tech timing, and gas/mineral opportunity cost from replays or a verified recorder. The present telemetry module does not infer those metrics.
- **Stage 4:** adapt the same pipeline to Scout tank-assassination scenarios, Guardian redesign and Greater Spire tempo, with fresh baselines.

**Integrity gates:** Pluto crashes are never assigned losses; unlabeled/manual outcomes are kept out of artifact-backed summaries; unexpected file layouts and shared Flingy references fail closed. A race's changed win rate alone cannot establish which mechanic caused it if the original openings, map positions, or opponent behavior differ.

## Devourer 2×2 movement/cooldown variants

The [factorial experiment protocol](DEVOURER_FACTORIAL.md) adds standalone speed-only and cooldown-only conditions and the combined `devourer_speed_550_cooldown_90` condition. The new `mod_factorial.py` reads the original cooldown from a user-extracted `weapons.dat`, then applies 90% rounded to integral frames. It emits two asset files and a hash receipt per variant. The established speed-only patcher and baseline remain unchanged. Exercise separate movement and attack-cadence smoke tests before the combined arm and Pluto match experiments.

## Stage 3: observational calibration beside Pluto

The [BWAPI tournament sidecar](live_trace/README.md) adds per-frame read-only observation of unit velocity, firing events and cooldown resets, Acid Spore stacks and target distance while Pluto stays the primary AI. Windows x86 syntax compilation is CI-checked against pinned BWAPI v4.4.0 headers. Full DLL linkage and actual native gameplay remain required.

The [trace analyzer](world_model/trace_analysis.py) validates bounded JSONL and reports movement speed, attack intervals, spore changes, visible-formation separation, and the fraction of cooldown-ready frames spent with a visible air target outside nominal weapon range. The [calibration gate](world_model/calibration_gate.py) demands repeated observations in all four interventions and outputs a proposal without changing world-model constants. The [provenance binder](world_model/provenance.py) compares observed RNG seed, player start tile and BWAPI map SHA-1 to the experiment manifest and parsed replay.

The sidecar uses the normal BWAPI tournament interface. Controlled-game and Pluto performance-neutrality tests on the actual Windows installation are the remaining acceptance checks.
