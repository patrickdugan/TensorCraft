# Stage 1: Devourer mod and Pluto smoke protocol

**State: documented, not executed.** Requires legally owned StarCraft: Brood War 1.16.1 on Windows or Wine. Preserve original Blizzard assets privately.

## Isolation and compatibility

Pluto v2.0 was built for the original BW 1.16.1 + BWAPI 4.4.0, with a 32-bit DLL and 64-bit CPU inference process. Direct OpenBW compatibility is unverified. Its older binary was observed reading version-specific engine memory, so use native BW for the first Pluto tests.

OpenBW is useful for studying the engine but has no clearly published redistribution license. Our replay probe uses only the MIT-licensed replay-core crate from broodwar-live/bw-engine, not that project's restricted simulator.

## Construct balanced control and treatment MPQs

1. Copy your locally owned 1.16.1 game into two disposable private folders. Run an unmodified BWAPI + Pluto baseline game first.
2. Extract the effective original arr\units.dat and arr\flingy.dat with PyMS/PyMPQ, respecting the game's archive override precedence: Patch_rt.mpq, then BrooDat.mpq, then StarDat.mpq. A file may be absent from a higher-priority archive. Keep original MPQs read-only; save each extracted file in your private asset directory.
3. Run python -m starcraft_lab.mod inspect using both extracted files. Record SHA-256 values. Use the patch subcommand to create BOTH control and devourer_speed_550 variants, in separate new output folders, passing the two hashes as explicit --expect-* arguments.
4. In PyMS/PyMPQ, create a fresh minimal MPQ for each variant. Import its staged flingy.dat under the internal archive path arr\flingy.dat. The archive must have the same internal path as the original. Name packages differently, for example sc_lab_control.mpq and sc_lab_devourer550.mpq.
5. Extract the newly packaged arr\flingy.dat and compare SHA-256 with the patch receipt's output_sha256. Keep original Patch_rt.mpq untouched.
6. Use MPQDraft's Load MPQ Patch mode on the private copy of the executable. The BWAPI/Chaoslauncher injector may conflict with MPQDraft's injector; verify launcher coexistence rather than presuming it. Record failures separately.

References: [PyMS MPQ docs](https://github.com/poiuyqwert/PyMS/blob/master/Help/Files/MPQ.md), [StarEdit mod launching](https://wiki.staredit.net/wiki/Running_StarCraft_Mods), [Pluto](https://github.com/tscmoo/pluto), [BWAPI](https://github.com/bwapi/bwapi).

## Static preflight

From the repository root, run the following in one shell command (paths are examples):

python -m starcraft_lab.preflight --game-dir C:\bw-exp\variant --units C:\bw-assets\units.dat --original-flingy C:\bw-assets\flingy.dat --staged-flingy C:\bw-exp\variant-payload\flingy.dat --receipt C:\bw-exp\variant-payload\flingy.dat.receipt.json --mpq-extracted-flingy C:\bw-exp\variant-extracted\flingy.dat --output C:\bw-exp\variant-preflight.json

The audit hashes the binary, Pluto files, original MPQs and balance data. It checks PE architectures and the BWAPI ai entry. A successful static report **does not establish** that the executable is 1.16.1, the CPU supports AVX2, Pluto comes from the official v2 release, the custom MPQ was loaded, or the two injectors coexist.

## Live movement proof

Use a private test map with a preplaced Devourer and an unobstructed straight flight path. Position and frame-count readings from an independent BWAPI observer are preferable to visual judgment. The declared baseline is 5.00 pixels/frame and treatment is 5.50. Measure steady-state displacement after acceleration settles, keeping identical start/target positions. Include a control package to exercise the same launch method under both variants. Record the map hash, exact package hash, setup coordinates, measurement window and whether the correct movement table was loaded.

## Pluto A/B protocol

Confirm Pluto playing an unmodified game, then with the control MPQ and modified MPQ. Use separate private game installations and per-opponent bandit files to prevent cross-contamination. Every matched pair must preserve game version, model, hardware, map, opponent, starting seed, initial bandit state and spawn side. Mirror spawn positions in blocks. Store game outcome and crashes separately. Start with 30 matched pairs as exploratory data. Pluto's learned policy is calibrated to standard game rules, so a unit speed buff may change outcomes while leaving its strategic adoption unchanged.

## Parse and validate replays

Run this Rust command from the repository root (requires Rust stable):

cargo run --release --manifest-path starcraft_lab/replay_probe/Cargo.toml -- C:\bw-exp\replays\game.rep C:\bw-exp\parsed\game.json

Then run the Python command using your per-run manifest and Pluto stats log:

python -m starcraft_lab.telemetry record --manifest C:\bw-exp\manifests\game.json --pluto-log C:\bw-exp\logs\pluto_bandit_OPPONENT.txt --result-evidence-file C:\bw-exp\replays\game.rep --replay-report C:\bw-exp\parsed\game.json --output C:\bw-exp\records\game.json

Aggregate records with:

python -m starcraft_lab.telemetry analyze --records C:\bw-exp\records

Set agent_player_id in each manifest to the replay parser's player_id assigned to Pluto. Use actual hashes rather than placeholders. The probe checks reported winner against a first-leaver heuristic and yields Devourer, Greater Spire, Guardian, Scout and opponent-defense **order** statistics. It can estimate production starts and tracks decoding warnings. Commands do not establish completed units, survival, damage, positional control or in-combat acid spore applications. Those measurements need independent game-state telemetry.

## Reproducibility cautions

The trial manifest's map_sha256 hashes the exact map file used for play, while the replay probe's chk_sha256 hashes its embedded uncompressed CHK map data. These are distinct representations and generally have distinct hashes. Verify that both variants have consistent map identity. The Pluto bandit stats log can contain records for many games against an opponent. The collector currently preserves those JSON events as an unfiltered source archive; do not attribute its entire trajectory to one game until record IDs or match boundaries are independently reconciled.
