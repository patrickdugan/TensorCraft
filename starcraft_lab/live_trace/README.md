# Stage 3: Passive BWAPI live calibration

**State:** C++ tournament sidecar authored; native x86 compilation and a real Pluto match are still required. Offline Python trace-consumer tests run in GitHub Actions. This is a research diagnostic, not a game modification.

## Why a tournament module

BWAPI 4.4.0 has a separate TournamentModule and `newTournamentAI()` interface. Its engine source dispatches frame events to the main AI first and then to the tournament AI, allowing post-frame snapshots alongside Pluto. This is a preferable observation surface to injecting hooks into Pluto's proprietary binary. The observer contains no game orders, network I/O, game-speed changes or fog-disabling flag calls.

**Source references:**

- [BWAPI TournamentModule/AIModule API](https://github.com/bwapi/bwapi/blob/main/bwapi/include/BWAPI/AIModule.h)
- [BWAPI event dispatch](https://github.com/bwapi/bwapi/blob/main/bwapi/BWAPI/Source/BWAPI/GameEvents.cpp)
- [Tournament module exported factories](https://github.com/bwapi/bwapi/blob/main/bwapi/ExampleTournamentModule/Source/Dll.cpp)
- [Unit fields and status accessors](https://github.com/bwapi/bwapi/blob/main/bwapi/include/BWAPI/Unit.h)
- [BWAPI sample tournament configuration](https://github.com/bwapi/bwapi/blob/main/Release_Binary/Starcraft/bwapi-data/bwapi.ini)

## Build locally with x86 MSVC

Use the **same** tagged BWAPI 4.4.0 headers and static BWAPILIB build as Pluto's native game integration. Check the actual release artifacts and local library paths before building. The tool deliberately fails its CMake configure step on non-Windows or non-x86 environments. The code now also exports newAIModule as a passive no-op AI for watching real replay files with BWAPI auto-menu and native JSON start/end markers; in normal Pluto matches, use the original ai=pluto.dll and the same DLL only in the tournament field.

Run from a Visual Studio developer PowerShell or cmd with Win32 build tools:

```powershell
cmake -S starcraft_lab/live_trace -B C:\BWLab\build\trace -G "Visual Studio 17 2022" -A Win32 -DBWAPI_INCLUDE_DIR="C:\dev\bwapi\bwapi\include" -DBWAPILIB_LIBRARY="C:\dev\bwapi\out\Release\BWAPILIB.lib"
cmake --build C:\BWLab\build\trace --config Release
```

Build BWAPILIB using the exact BWAPI v4.4.0 tag, with the generated svnrev.h from the project's versioned git history (see the passing Windows CI workflow). Ensure the observer uses the same /MD dynamic C runtime as BWAPILIB and targets Win32/x86. The CMake paths above are **examples**. Resolve the actual built `BWAPILIB.lib` or the compiler's corresponding output from the pinned local checkout. Verify the produced `TensorCraftTrace.dll` is an x86 DLL, and include its SHA-256 in the match receipt. A successful compile is one gate; it does not establish game compatibility or acceptable performance overhead.

## Enable on a disposable private installation

Keep the existing Pluto AI entry in the private copy of `bwapi-data/bwapi.ini`, and add the tournament path in its `[ai]` section:

```ini
[ai]
ai = bwapi-data/AI/pluto.dll
tournament = C:\BWLab\tools\TensorCraftTrace.dll
```

BWAPI tournament modules require RELEASE mode. Leave the original installation and its INI unchanged. Launch the executable from a command prompt whose environment contains the trace settings. MPQDraft can load the modified DAT archive and BWAPI's MPQDraft injector in the same session:

```cmd
set TC_TRACE_PATH=C:\BWLab\runs\test-control-001.jsonl
set TC_TRACE_RUN_ID=test-control-001
set TC_TRACE_VARIANT=control
start "" "C:\BWLab\tools\MPQDraft.exe"
```

Choose LOAD MPQ PATCH, the copied 1.16.1 executable, the specified control/treatment MPQ, and the BWAPI injector plugin. Inspect the game logs for Pluto's advertised startup and tournament module loading; if the sidecar has no completed trace, record the failure rather than creating an inferred success.

**Trace files must have new absolute local paths**. The observer uses Windows `CREATE_NEW` to refuse overwrites, blocks UNC network paths, and avoids hidden game-folder file writes. Missing environment settings disable recording. It caps the trace at 128 MiB, checks per-frame unit limits, and marks truncated output. Flushes every 128 frames; a crash may leave an incomplete tail, which the reader rejects.

## Recorded observables

Each JSONL trace starts with provenance (`run_id`, `variant`, `producer`, `replay_mode`, map-information flag) followed by per-frame snapshots and one explicit completion event. Selected air units and relevant ground anti-air have player/unit IDs, unit type, position, velocity, HP/shields, air cooldown, attack-start and attack-frame flags, Acid Spore stacks, and target ID.

The tournament module **does not enable complete map information**; its visible enemy data are restricted by BWAPI's normal player information. If another component enables full map information, the trace records that state and the strict analyzer rejects it for normal fog-limited calibration. An out-of-view enemy may be absent. Unit absence alone does not prove death.

## Read/measure the trace

```powershell
python -m starcraft_lab.world_model.trace_analysis report --trace C:\BWLab\runs\test-control-001.jsonl --strict --output C:\BWLab\reports\test-control-001.json
```

For a four-arm comparison, generate one report for each isolated run, place them in the same private directory, then:

```powershell
python -m starcraft_lab.world_model.trace_analysis compare --reports C:\BWLab\reports --output C:\BWLab\reports\four-arm-comparison.json
```

The offline analyzer rejects malformed JSON, out-of-order frames, duplicate unit IDs, missing completion, oversized records and invalid unit fields. In strict mode it also rejects missing frame numbers, unit-count truncation or full-map information. It emits observed speed percentiles, attack-start intervals, cooldown-reset samples, Acid Spore additions/removals and formation distances.

## Physics acceptance experiments

1. **Control movement:** preplace a Devourer in a private scenario with an unobstructed straight-line travel route; record enough frames to reach steady velocity. Compare high-percentile velocity to the expected 5.00 px/frame. Repeat with speed-only 5.50.
2. **Control attack:** give the Devourer a stationary, durable air target, so it attacks repeatedly. Compare multiple detected attack-start intervals and air cooldown resets. BWAPI documents per-attack jitter of about -1 to +2 frames; target changes/animation can introduce longer delays. Compare original versus 90% cooldown arms.
3. **Combined:** confirm both movement and cadence together. Run one- and two-Devourer Muta compositions against matched enemies while tracking sporess on continuously visible targets.
4. **Acid Spore:** record stack increments and earliest decrements with a known target. Infer stack-decay timing only after enough continuous visibility and repeated isolated applications. Shared-target/focus-firing effects cannot be established from the counter values alone.
5. **Pluto overhead:** verify the sidecar's sample frequency does not alter Pluto's inference cadence, produce timeouts, corrupt replays, or change a control baseline. Compare with uninstrumented Pluto games before treating results as behaviorally neutral.

The analysis purposely does not directly overwrite the world model's physical constants. A live trace must be linked to exact game/agent/mod/map hashes, and its observer limitations reviewed, before it becomes calibration evidence. A source-reported `win` flag is an outcome for the current game player, and should be checked against replay/referee results independently.

## Experimental handoff

The live-telemetry branch is stacked on [World Model PR #3](https://github.com/patrickdugan/TensorCraft/pull/3) and ultimately on [Devourer Mod PR #1](https://github.com/patrickdugan/TensorCraft/pull/1). The existing batch experiment, legacy replay parser, and proprietary game-file boundaries remain unchanged.


## Four-arm parameter consistency gate

Collect **at least two independent, contiguous** native traces per variant, with at least 450 observed frames, 60 moving Devourer velocity samples, and five detected attack intervals in each trace. Use the same controlled map and a durable air target so attack cadence is measurable.

After converting the JSONL traces to report JSONs, place the corresponding *privately generated* `mod_factorial.py` receipts in directories `control/receipt.json`, `devourer_speed_550/receipt.json`, `devourer_cooldown_90/receipt.json`, and `devourer_speed_550_cooldown_90/receipt.json`. Run:

```powershell
python -m starcraft_lab.world_model.calibration_gate --reports C:\BWLab\reports --receipt-root C:\BWLab\mods --output C:\BWLab\reports\calibration-proposal.json
```

The gate compares observed high-percentile Devourer velocity with 5.0/5.5 px/frame, and median attack-start intervals with 100/90 frames. It also checks declared receipt speed/cooldown values, expected output hashes, unique run IDs, and sufficient repeated measurements. Any incomplete condition produces an explicit diagnostic rather than an applied model update.

**A positive gate is still pending native identification**: the sidecar's run-id and variant strings are environment claims; its file hashes and receipts must be checked against the actual MPQDraft-loaded payload and replay hashes. This gate deliberately leaves the original surrogate model unchanged. The full performance and non-interference assessment belongs in a live match with and without the tournament sidecar.

## Bind the trace to a planned match

BWAPI exposes `getRandomSeed`, `mapHash`, the current player's starting tile, and the player ID. The latest sidecar writes these **observed game values** into its header, along with the previous frame-level telemetry. Its `map_sha1` is BWAPI's SHA-1 of the loaded map archive on ordinary melee maps, distinct from the experiment manifest's SHA-256.

After analyzing the trace and replay, create a private JSON map from your planned spawn-labels to actual map start tiles, e.g. `{"A":[4,7],"B":[118,7]}` after verifying the real map. Call:

```powershell
python -m starcraft_lab.world_model.provenance --manifest C:\BWLab\completed\RUN.json --trace-report C:\BWLab\reports\RUN.json --replay-report C:\BWLab\parsed\RUN.json --map-file C:\BWLab\maps\map.scx --spawn-tiles C:\BWLab\spawn_tiles.json --output C:\BWLab\reports\RUN-binding.json
```

The provenance binder checks `run_id`, intervention, player ID, replay hash, approximate frame extent, observed initial RNG seed against the plan, observed start tile against the supplied mapping, and BWAPI map SHA-1 against the actual source map file. A mismatch invalidates strict pairing, and the output identifies the exact problem.

**Critical:** The existing TensorCraft batch planner labels a desired RNG seed and spawn but has not proved control over them. If observed seeds or starting tiles differ, treat those games as independent randomized observations rather than falsely pairing them on common seed. Pluto's executable identity and MPQ loading still require separate live preflight and speed/cooldown acceptance; an observed map hash cannot attest those binaries.

Record both `main_ai_event_ms` and `logical_fps` when the observer is enabled. Compare with games recorded without the observer; this is a non-interference check, while `main_ai_event_ms` by itself measures the callback duration rather than the whole 64-bit Pluto inference process.

## Firing-opportunity friction diagnostic

An additional observation tracks a Devourer whose air cooldown is zero, which is in an attacking state and has a visible airborne target. For those frames it calculates whether the target is beyond the declared air-weapon range, and reports a **cooldown-ready but target-out-of-range fraction**. It provides a direct hypothesis for why faster movement might help a Muta flock: the Devourer could spend fewer attack-ready frames chasing enemies.

The default nominal range is 192 pixels (6 build tiles) in the public catalog. Replace it with the locally extracted `weapons.dat.max_range` when available:

```powershell
python -m starcraft_lab.world_model.trace_analysis report --trace C:\BWLab\runs\run.jsonl --strict --devourer-range-px 192 --output C:\BWLab\reports\run.json
```

The statistic excludes targets outside player visibility and any frame with truncated units. Weapon readiness and proximity still do not establish a guaranteed shot; attack animation, targeting rules, cooldown randomness and projectile travel remain separate variables.
