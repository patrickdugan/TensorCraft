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

Use the **same** tagged BWAPI 4.4.0 headers and static BWAPILIB build as Pluto's native game integration. Check the actual release artifacts and local library paths before building. The tool deliberately fails its CMake configure step on non-Windows or non-x86 environments.

Run from a Visual Studio developer PowerShell or cmd with Win32 build tools:

```powershell
cmake -S starcraft_lab/live_trace -B C:\BWLab\build\trace -G "Visual Studio 17 2022" -A Win32 -DBWAPI_INCLUDE_DIR="C:\dev\bwapi\bwapi\include" -DBWAPILIB_LIBRARY="C:\dev\bwapi\out\Release\BWAPILIB.lib"
cmake --build C:\BWLab\build\trace --config Release
```

The CMake paths above are **examples**. Resolve the actual built `BWAPILIB.lib` or the compiler's corresponding output from the pinned local checkout. Verify the produced `TensorCraftTrace.dll` is an x86 DLL, and include its SHA-256 in the match receipt. A successful compile is one gate; it does not establish game compatibility or acceptable performance overhead.

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
