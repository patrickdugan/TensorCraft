//! Standalone MIT replay-core adapter for asset-free StarCraft experiments.
//! Reports commands, not battlefield state. Does not link bw-engine simulation.
use replay_core::analysis::BuildAction;
use replay_core::metadata::GameResult;
use replay_core::Replay;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::path::Path;

const UNITS: &[(u16, &str)] = &[
    (62, "devourer"), (44, "guardian"), (43, "mutalisk"),
    (137, "greater_spire"), (70, "scout"), (84, "observer"),
    (5, "siege_tank_tank_mode"), (30, "siege_tank_siege_mode"), (3, "goliath"),
];

fn unit_type(action: &BuildAction) -> Option<u16> {
    match action {
        BuildAction::Train(unit)
        | BuildAction::UnitMorph(unit)
        | BuildAction::BuildingMorph(unit)
        | BuildAction::Build(unit) => Some(*unit),
        _ => None,
    }
}

/// Issued orders are intentions. Model-predicted starts are fallible.
/// Neither establishes unit survival, positional control, or combat.
fn unit_orders(replay: &Replay, player_id: u8) -> Value {
    let mut results = serde_json::Map::new();
    for &(unit_id, label) in UNITS {
        let ordered: Vec<_> = replay.build_order.iter()
            .filter(|e| e.player_id == player_id && unit_type(&e.action) == Some(unit_id))
            .collect();
        let predicted_starts: Vec<_> = replay.production.iter()
            .filter(|e| e.player_id == player_id
                && unit_type(&e.action) == Some(unit_id)
                && !e.dropped && e.start_frame.is_some())
            .collect();
        let estimated_count: u32 = predicted_starts.iter().map(|e| e.count).sum();
        results.insert(label.to_string(), json!({
            "unit_type_id": unit_id,
            "issued_orders": ordered.len(),
            "first_order_frame": ordered.iter().map(|e| e.frame).min(),
            "model_estimated_starts": estimated_count,
            "first_model_estimated_start_frame":
                predicted_starts.iter().filter_map(|e| e.start_frame).min(),
        }));
    }
    Value::Object(results)
}

fn report(bytes: &[u8]) -> Result<Value, String> {
    let replay = replay_core::parse(bytes).map_err(|e| format!("replay parse failure: {e}"))?;
    let warnings = &replay.command_warnings;
    let winner = match &replay.metadata.result {
        GameResult::Winner { player_id, .. } => Some(*player_id),
        GameResult::Unknown => None,
    };
    let players: Vec<_> = replay.header.players.iter().map(|p| {
        let apm = replay.player_apm.iter().find(|e| e.player_id == p.player_id);
        json!({
            "player_id": p.player_id,
            "name": p.name,
            "race": format!("{:?}", p.race),
            "apm": apm.map(|a| a.apm),
            "eapm": apm.map(|a| a.eapm),
            "unit_orders": unit_orders(&replay, p.player_id),
        })
    }).collect();
    Ok(json!({
        "schema": 1,
        "producer": "starcraft-replay-probe/replay-core",
        "parser_revision": "c1014fa068a927c3c9f09fc5cea7be157f9f5742",
        "replay_sha256": format!("{:x}", Sha256::digest(bytes)),
        "chk_sha256": format!("{:x}", Sha256::digest(&replay.map_data)),
        "map_name": replay.header.map_name,
        "map_width_tiles": replay.header.map_width,
        "map_height_tiles": replay.header.map_height,
        "frame_count": replay.header.frame_count,
        "game_speed": format!("{:?}", replay.header.game_speed),
        "engine_type": format!("{:?}", replay.header.engine),
        "command_count": replay.commands.len(),
        "command_warnings": {
            "clean": warnings.is_clean(),
            "unknown_commands": warnings.unknown_commands,
            "truncated_commands": warnings.truncated_commands,
            "skipped_bytes": warnings.skipped_bytes,
        },
        "winner_player_id": winner,
        "winner_inference": "first_leaver_heuristic",
        "players": players,
        "limitations": ["orders_are_not_completions", "no_unit_positions", "no_survival_or_damage_inference"],
    }))
}

fn run() -> Result<(), String> {
    let mut args = env::args_os().skip(1);
    let input = args.next().ok_or("usage: starcraft-replay-probe GAME.rep [OUTPUT.json]")?;
    let output = args.next();
    if args.next().is_some() { return Err("unexpected extra arguments".into()); }
    let path = Path::new(&input);
    let size = fs::metadata(path).map_err(|e| format!("file metadata: {e}"))?.len();
    if size > 64 * 1024 * 1024 { return Err("replay exceeds 64 MiB limit".into()); }
    let bytes = fs::read(path).map_err(|e| format!("read replay: {e}"))?;
    let value = report(&bytes)?;
    let mut encoded = serde_json::to_vec_pretty(&value).map_err(|e| format!("encode JSON: {e}"))?;
    encoded.push(b'\n');
    if let Some(out) = output {
        let out_path = Path::new(&out);
        if out_path == path { return Err("output would overwrite source replay".into()); }
        let mut handle = fs::OpenOptions::new().write(true).create_new(true)
            .open(out_path).map_err(|e| format!("refusing output: {e}"))?;
        use std::io::Write;
        handle.write_all(&encoded).map_err(|e| format!("write JSON: {e}"))?;
    } else {
        print!("{}", String::from_utf8_lossy(&encoded));
    }
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn classification_tracks_morph_and_build() {
        assert_eq!(unit_type(&BuildAction::UnitMorph(62)), Some(62));
        assert_eq!(unit_type(&BuildAction::BuildingMorph(137)), Some(137));
        assert_eq!(unit_type(&BuildAction::Train(70)), Some(70));
        assert_eq!(unit_type(&BuildAction::Research(12)), None);
    }
    #[test]
    fn invalid_replay_fails() {
        assert!(report(b"arbitrary bytes").is_err());
    }
}
