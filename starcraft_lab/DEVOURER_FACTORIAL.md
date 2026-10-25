# Devourer speed and cooldown factorial experiment

Four variants are supported by starcraft_lab/mod_factorial.py:

| Variant | Speed | Cooldown |
| --- | --- | --- |
| control | 5.00 px/frame | original |
| devourer_speed_550 | 5.50 px/frame | original |
| devourer_cooldown_90 | 5.00 px/frame | 90% of original (rounded to integral frames) |
| devourer_speed_550_cooldown_90 | 5.50 px/frame | 90% of original (rounded to integral frames) |

The original weapon cooldown is read dynamically from the owned game's weapons.dat. Unit 62's air weapon ID is read from units.dat, and the cooldown column is located using the upstream OpenBW data-loading layout. The original numbers are recorded in the output receipt. Input SHA-256 mismatches, weapon aliasing, invalid layouts and unexpected movement baselines fail closed.

## Generation

Extract arr/units.dat, arr/flingy.dat and arr/weapons.dat from legally owned Brood War 1.16.1 data, obeying archive override priority. Keep inputs read-only. Obtain their SHA-256 hashes and run:

```bash
python -m starcraft_lab.mod_factorial --units /private/units.dat --flingy /private/flingy.dat --weapons /private/weapons.dat --variant devourer_speed_550_cooldown_90 --expect-units-sha256 UNITS_SHA --expect-flingy-sha256 FLINGY_SHA --expect-weapons-sha256 WEAPONS_SHA --output-dir /private/combined
```

Repeat for each variant with a separate output directory. Every directory contains flingy.dat, weapons.dat and receipt.json. Each private mod MPQ must embed both files at arr\flingy.dat and arr\weapons.dat. Extract the packaged contents and compare both output hashes before loading the private game copy through BWAPI's native MPQDraft plugin.

## Measurement order

First confirm the standalone speed-only changes actual movement, and that standalone cooldown-only changes the observed attack cadence, using a fixed air-combat test scenario. Confirm the combined arm exhibits both effects. The current replay parser sees issued commands but does not reconstruct damage, attack timestamps, unit positioning, or acid-spore status.

Then test balanced Mutalisk flocks with zero, one and two Devourers under each arm. Compare chase/disengagement ability, engagement timing, damage/acid application, surviving army value and Muta flock cohesion. For match-level Pluto evaluation use a controlled 2x2 design, reset its per-opponent opening bandit each trial, and separate speed and cooldown main effects from their interaction. The original client applies the modification globally, so a ZvZ opponent may receive the same buff.

Stage 1 native Pluto/BWAPI mod compatibility and the actual in-game movement/cooldown checks remain unverified. Keep the existing baseline and original speed-only patch unchanged.
