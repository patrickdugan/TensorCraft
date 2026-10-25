# TensorCraft Headless Tournament Farm + YouTube Replay Renderer

**Status: asset-free orchestration implemented, native headless Pluto/mod launch unverified.** The tournament manager and renderer deliberately refuse to claim real gameplay from a successful process exit or a nonempty video file. This code is stacked above the existing [live telemetry research](../live_trace/README.md), world model, and four-arm Devourer patch.

## Architecture and why there are two execution lanes

```text
                 planned A/B experiments
                           |
         deterministic pair allocation / CPU budget
          /                |                \
    headless slot 1   headless slot 2   headless slot N
    private game      private game      private game
    private wine      private wine      private wine
    private bandit    private bandit    private bandit
          \                |                /
                 replays + telemetry
                           |
            Rust replay-core validation
                           |
             top-N interesting replays
                           |
       private virtual Xvfb screen (one per renderer)
       original BW 1.16.1 + matching mod MPQ
       Wine GUI replay playback -> FFmpeg x11grab
                           |
       1920x1080/30p H.264 + AAC-silence MP4
                           |
         FFprobe + human gameplay visual review
```

Headless matchmaking does **not** create the YouTube images. Only real engine replay playback through an actual (possibly virtual) graphical display does that. The headless match tier can use `bwheadless` or a supported tournament manager. The video tier cannot use `bwheadless` as a render engine because it does not render replay graphics. This is consistent with [SSCAIT sc-docker](https://github.com/Games-and-Simulations/sc-docker), [the BWAPI 4.4.0 project](https://bwapi.github.io/), [Pluto's supported runtime](https://github.com/tscmoo/pluto), and the documented [observer-docker replay playback limitation](https://github.com/ElsevierSoftwareX/SOFTX-D-25-00542).

The older sc-docker container recipes use an obsolete Ubuntu/Wine stack; use them as integration references rather than assuming they run Pluto v2.0's 32-bit DLL and separate 64-bit CPU inference executable on modern machines. Do not distribute a private proprietary Blizzard game installation inside this public Git repository.

## Parallel game execution

Create disposable, **pre-provisioned** BW 1.16.1 installations for every worker, each with its own Wine prefix, BWAPI settings, Pluto files, map directory, and allocated network ports. Installers and the custom MPQDraft/bwheadless integration remain private to the machine running the experiments. The engine and bot files must match the pinned hashes of the [existing batch experiment](../BATCH.md).

The runner needs an external native match-launch **adapter** implementing the contract in [runtime.example.json](runtime.example.json):

- Its argv receives `{run_id}`, `{map_path}`, `{mod_mpq}`, `{replay_path}` and optional `{game_dir}`, `{wineprefix}`, `{display}`, `{port_base}`, `{variant}`, `{seed_label}`.
- It must actually load the declared MPQ into the native BW 1.16.1 game (so a replay's weapon/movement changes are genuine), start two participants, wait for the game to finish, save the replay at the requested new path, then exit with a meaningful status code.
- It must launch Pluto v2.0 with BWAPI 4.4.0 and a separate x64 inference process. A headless Wine session may still require a minimal X11 display for startup; supply it in the adapter.
- A requested seed is not proof that BW used that seed. The existing [live trace attestation](../world_model/provenance.py) checks real RNG, start tile and map hash after gameplay.
- A failed/aborted match remains `launcher_failed` or `launcher_timeout`; the runner never converts errors into losses.

Pluto's per-opponent bandit file is mutable. Each worker must have private copies in its game directory. The runner backs up any pre-existing private bandit files, writes the **same hash-pinned starting snapshot before each match**, and executes both arms of each pair sequentially on the same slot. Different pairs may run concurrently on different slots. Slots cannot share an installation, Wine prefix, X11 display, or base network port.

### Commands

Start by generating the existing experiment plan against your legally owned local assets:

```bash
python -m starcraft_lab.batch plan --spec /private/BWLab/experiment.json --output /private/BWLab/plan
```

Copy [runtime.example.json](runtime.example.json) to an **outside-repo** location, replace paths/hashes, and provision each private worker. This example is an interface declaration, not a working bwheadless adapter. Prepare without starting native programs:

```bash
python -m starcraft_lab.farm.runner prepare --plan /private/BWLab/plan/plan.json --runtime /private/BWLab/runtime.json --output /private/BWLab/runs/devourer_001
```

After a real native adapter successfully passes a single-match acceptance test:

```bash
python -m starcraft_lab.farm.runner run --workspace /private/BWLab/runs/devourer_001 --allow-native
```

The result consists of per-run replay candidates, log files, first-party SHA-256 manifests and statuses. It only marks replays `parsed_replay` when the prebuilt Rust `starcraft-replay-probe` accepts their complete command stream and hashes; this remains distinct from a referee-confirmed win, an authenticated Pluto binary, or proof that the exact mod MPQ was active.

## CPU and memory budget

Pluto v2.0 recommends at least six CPU cores and approximately 2 GB of **free** RAM for its inference engine; a match generally has more than one StarCraft process, plus game buffers and Wine. Start conservatively at **one match**, then test two independent matches if your real CPU, RAM, inference timing and frame responsiveness permit. The example sets two slots with a declared 12-thread and 16-GB aggregate budget. These are capacity-planning inputs, **not operating-system resource quotas**. Increase workers only after measuring physical memory, CPU utilization and Pluto inference latency. The video lane consumes CPU/GPU encoding time too; generally keep rendering separate from bulk matching on a 32-GB machine.

## YouTube capture from actual replay playback

Video production uses a **different executable path** from headless gameplay. Prepare a separate graphical Wine/Brood War 1.16.1 installation, under the same patch and version. Its external GUI playback wrapper must:

1. Accept the exact `.rep` path and **matching** custom mod MPQ, and launch a real graphical StarCraft replay on the Xvfb display reserved for that worker.
2. Create the new `{ready_marker}` file only after actual replay playback has visibly begun. The renderer records only after the marker exists.
3. Create `{done_marker}` only after reaching the actual replay end. Without this the result is partial or failed, never a full-game recording.
4. Use isolated Wine prefixes, game copies and display numbers for independent render workers. A wrapper can use VNC or `xdotool` to select the replay, but the historical observer-docker project reports that GUI replay selection has required manual intervention in some builds. Therefore native automation requires a dedicated smoke test.

The renderer launches its own access-controlled Xvfb display and invokes FFmpeg `x11grab`; no physical monitor needs to be connected. It scales the native 640x480 **4:3** image to 1440x1080 without stretching, centers it inside 1920x1080 with pillarboxing, then encodes H.264 yuv420p 30fps with faststart MP4 metadata and AAC stereo **silence**. The capture doesn't include authentic StarCraft game audio yet; audio capture/mixing or voice-over is an independent publication pass.

Select only replays that pass the Rust parser and prioritize ones where Devourer, Mutalisk, Guardian, Scout and anti-air build orders make for interesting story beats:

```bash
python -m starcraft_lab.farm.render_queue select --workspace /private/BWLab/runs/devourer_001 --output /private/BWLab/selection --max-videos 3
```

Copy [render_pool.example.json](render_pool.example.json) to a private file. It deliberately names an external `replay_gui_wrapper` whose actual implementation must be verified on the target. A single video job can also be described using [video.example.json](video.example.json). After native GUI playback is proven:

```bash
python -m starcraft_lab.farm.render_queue render --selection /private/BWLab/selection --pool /private/BWLab/render_pool.json --output /private/BWLab/videos --execute
```

Or capture one verified replay explicitly:

```bash
python -m starcraft_lab.farm.video capture --config /private/BWLab/one_video.json --output-dir /private/BWLab/videos/RUN_001 --execute
```

To check a resulting video technically:

```bash
python -m starcraft_lab.farm.video verify --video /private/BWLab/videos/RUN_001/RUN_001.mp4
```

The verifier checks MP4 video codec, 1920x1080 dimensions, 30 fps, yuv420p pixel format, one stereo AAC 48kHz audio stream, positive duration, source hash and `moov`-before-`mdat` faststart structure. It **cannot prove that the captured pixels are authentic Brood War replay footage, that the target replay finished without desync, or that the expected mod was loaded**. The full-game acceptance check and publication review remain explicit.

## A video codec proof before any game installation

This runs on an ordinary Linux machine with FFmpeg/libx264 and ffprobe:

```bash
mkdir -p /tmp/tensorcraft_youtube_codec
python -m starcraft_lab.farm.smoke_video --output /tmp/tensorcraft_youtube_codec/SYNTHETIC_NOT_GAMEPLAY.mp4
```

The CI equivalent attaches a clearly labeled **synthetic FFmpeg test-pattern video**, and validates the result through FFprobe. It proves that the output file format works, not that StarCraft graphical replay launch already works.

## Replay fairness and data lineage

Every replay's full source file, map SHA-256, mod MPQ SHA-256, native executable hashes, batch manifest, initial Pluto bandit hash, and independent Rust parser report must be retained. Modded replays have to be played back with matching game rules and actual data overrides, otherwise rendered behavior may diverge. The existing world-model data and [native provenance binder](../world_model/provenance.py) provide additional cross-checks. Never upload Blizzard MPQs, Pluto proprietary binaries, or raw private personal identifiers with the published YouTube package.

## Acceptance gates before any real YouTube upload

- **Gate A: Headless worker.** One native BW 1.16.1 + Pluto v2.0 game finishes without GUI, emits a replay that Rust replay-core parses and captures an observed-game-start/result record; the expected global mod effect has been demonstrated in-engine.
- **Gate B: Isolation.** Two workers finish independent games concurrently without sharing bandit files, game executables, Wine prefixes, ports, or X11 display, and with stable inference latency.
- **Gate C: Playback.** The actual modded `.rep` plays to completion in a virtual graphical Wine display; an independent reviewer confirms representative gameplay frames and start/end state.
- **Gate D: Video.** FFprobe passes 1080p H.264/AAC faststart checks, and an actual visual review confirms continuous correct StarCraft content. Confirm the expected 4:3 aspect ratio, readable UI, and correct copyright-clear soundtrack or intentional silence.
- **Gate E: Reproducibility.** Match and capture manifests identify exact replay, mod, executable and encoder binaries, encode parameters, and the native/provenance verification limitations.

### Existing external precedents

- [sc-docker](https://github.com/Games-and-Simulations/sc-docker): headless BWAPI tournament bots under Wine; old container stack, no promise of Pluto v2 behavior.
- [Pluto](https://github.com/tscmoo/pluto): explicit Wine, 64-bit inference and bandit constraints; no GPU required for inference.
- [BWAPI](https://bwapi.github.io/): exact 1.16.1 / 4.4 runtime prerequisites.
- [Observer replay project](https://github.com/ElsevierSoftwareX/SOFTX-D-25-00542): GUI replay recorder, with manual replay selection limitations.
- [FFmpeg x11grab](https://ffmpeg.org/ffmpeg-devices.html#x11grab): supported capture of an X11 display.
