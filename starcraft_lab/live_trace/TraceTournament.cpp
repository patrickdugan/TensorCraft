// Read-only BWAPI tournament AI: snapshots while Pluto remains the normal AI.
// Build with 32-bit MSVC and the *same* BWAPI 4.4.0 headers/library as Pluto.
// This code has NOT been compiled against a Windows game installation in CI.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <BWAPI.h>
#include <cstdio>
#include <cstdint>
#include <string>
#include <io.h>
#include <fcntl.h>
#include <algorithm>

// Windows process environment, set before game launch:
//   TC_TRACE_PATH       Absolute new private file path, e.g. C:\BWLabs\r1.jsonl
//   TC_TRACE_RUN_ID     ASCII alphanumeric + _ . - up to 80 bytes
//   TC_TRACE_VARIANT    ASCII alphanumeric + _ . - up to 80 bytes
// A missing/invalid setting disables telemetry. No network or game commands.
namespace {
constexpr long MAX_OUTPUT_BYTES = 128L * 1024L * 1024L;
constexpr int MAX_UNITS_PER_FRAME = 160;
FILE* output = nullptr;
bool truncated = false;
unsigned long frames = 0;
std::string run_id;
std::string variant;
// GUI replay uses an independent, tiny marker-only channel. This works even
// when verbose combat traces are disabled to keep the video worker lightweight.
bool render_active = false;
bool render_started = false;
std::string render_run_id;
std::string render_ready_path;
std::string render_done_path;
bool match_active = false;
bool match_started = false;
std::string match_run_id;
std::string match_ready_path;
std::string match_done_path;

std::string read_env(const char* key) {
  char buf[1024] = {};
  const DWORD n = GetEnvironmentVariableA(key, buf, sizeof(buf));
  if (!n || n >= sizeof(buf)) return "";
  return std::string(buf, n);
}
bool safe_id(const std::string& s) {
  if (s.empty() || s.size() > 80) return false;
  for (const unsigned char c : s) {
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-')) return false;
  }
  return true;
}
bool absolute_windows_path(const std::string& s) {
  // No UNC path or relative path accepted, avoiding inadvertent network I/O.
  return s.size() >= 4 &&
    ((s[0] >= 'a' && s[0] <= 'z') || (s[0] >= 'A' && s[0] <= 'Z')) &&
    s[1] == ':' && (s[2] == '\\' || s[2] == '/');
}
bool write_render_marker(const std::string& path, const char* kind, int frame) {
  if (!render_active || !absolute_windows_path(path)) return false;
  HANDLE file = CreateFileA(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return false;
  char body[360] = {};
  const int n = std::snprintf(body, sizeof(body),
      "{\"schema\":1,\"producer\":\"bwapi_tournament_replay_marker\","
      "\"event\":\"%s\",\"run_id\":\"%s\","
      "\"replay_mode\":true,\"frame\":%d}\n",
      kind, render_run_id.c_str(), frame);
  DWORD written = 0;
  bool ok = n > 0 && n < (int)sizeof(body) &&
            WriteFile(file, body, (DWORD)n, &written, nullptr) && written == (DWORD)n;
  CloseHandle(file);
  return ok;
}
bool write_match_marker(const std::string& path, const char* kind, int frame) {
  if (!match_active || !absolute_windows_path(path)) return false;
  HANDLE file = CreateFileA(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return false;
  char body[360] = {};
  const int n = std::snprintf(body, sizeof(body),
      "{\"schema\":1,\"producer\":\"bwapi_tournament_match_marker\","
      "\"event\":\"%s\",\"run_id\":\"%s\","
      "\"replay_mode\":false,\"frame\":%d}\n",
      kind, match_run_id.c_str(), frame);
  DWORD written = 0;
  bool ok = n > 0 && n < (int)sizeof(body) &&
            WriteFile(file, body, (DWORD)n, &written, nullptr) && written == (DWORD)n;
  CloseHandle(file);
  return ok;
}
void close_output() {
  if (output) { std::fflush(output); std::fclose(output); output = nullptr; }
}
bool begin_output() {
  const std::string path = read_env("TC_TRACE_PATH");
  run_id = read_env("TC_TRACE_RUN_ID");
  variant = read_env("TC_TRACE_VARIANT");
  if (!absolute_windows_path(path) || !safe_id(run_id) || !safe_id(variant))
    return false;
  // CREATE_NEW means previously recorded data cannot be silently overwritten.
  HANDLE handle = CreateFileA(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
  if (handle == INVALID_HANDLE_VALUE) return false;
  const int fd = _open_osfhandle(reinterpret_cast<intptr_t>(handle), _O_BINARY | _O_WRONLY);
  if (fd < 0) { CloseHandle(handle); return false; }
  output = _fdopen(fd, "wb");
  if (!output) { _close(fd); return false; }
  // Buffered output reduces per-frame file-operation latency. It is flushed
  // every 128 frames, and at onEnd. A crash may lose a partial tail.
  std::setvbuf(output, nullptr, _IOFBF, 128 * 1024);
  return true;
}
int unit_owner(BWAPI::Unit unit) {
  BWAPI::Player p = unit->getPlayer();
  return p ? p->getID() : -1;
}
int target_id(BWAPI::Unit unit) {
  BWAPI::Unit target = unit->getTarget();
  return target && target->exists() ? target->getID() : -1;
}
bool tracked(BWAPI::Unit u) {
  if (u->isFlying() || u->getAcidSporeCount() > 0) return true;
  const BWAPI::UnitType t = u->getType();
  return t == BWAPI::UnitTypes::Terran_Goliath ||
         t == BWAPI::UnitTypes::Terran_Siege_Tank_Tank_Mode ||
         t == BWAPI::UnitTypes::Terran_Siege_Tank_Siege_Mode ||
         t == BWAPI::UnitTypes::Terran_Marine ||
         t == BWAPI::UnitTypes::Protoss_Dragoon;
}
}  // namespace

class TraceTournamentAI final : public BWAPI::AIModule {
 public:
  void onStart() override {
    frames = 0;
    truncated = false;
    close_output();
    render_started = false;
    render_active = false;
    render_run_id = read_env("TC_RENDER_RUN_ID");
    render_ready_path = read_env("TC_RENDER_READY_PATH");
    render_done_path = read_env("TC_RENDER_DONE_PATH");
    render_active = BWAPI::Broodwar->isReplay() && safe_id(render_run_id) &&
                    absolute_windows_path(render_ready_path) &&
                    absolute_windows_path(render_done_path) &&
                    render_ready_path != render_done_path;
    match_started = false;
    match_run_id = read_env("TC_MATCH_RUN_ID");
    match_ready_path = read_env("TC_MATCH_READY_PATH");
    match_done_path = read_env("TC_MATCH_DONE_PATH");
    match_active = !BWAPI::Broodwar->isReplay() && safe_id(match_run_id) &&
                   absolute_windows_path(match_ready_path) &&
                   absolute_windows_path(match_done_path) &&
                   match_ready_path != match_done_path;
    if (!begin_output()) return;
    const bool full = BWAPI::Broodwar->isFlagEnabled(BWAPI::Flag::CompleteMapInformation);
    BWAPI::Player self = BWAPI::Broodwar->self();
    const int player_id = self ? self->getID() : -1;
    const BWAPI::TilePosition start = self ? self->getStartLocation() : BWAPI::TilePosition(-1,-1);
    const unsigned rng_seed = BWAPI::Broodwar->getRandomSeed();
    std::string map_sha1 = BWAPI::Broodwar->mapHash();
    if (map_sha1.size() != 40 || !std::all_of(map_sha1.begin(),map_sha1.end(),
        [](unsigned char c) { return (c >= '0' && c <= '9') ||
                                      (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }))
      map_sha1.clear();
    std::fprintf(output,
      "{\"schema\":1,\"event\":\"start\",\"run_id\":\"%s\",\"variant\":\"%s\","
      "\"producer\":\"bwapi_tournament_trace_v1\",\"frame_step\":1,"
      "\"full_information_at_start\":%s,\"replay_mode\":%s,"
      "\"self_player_id\":%d,\"start_tile_x\":%d,\"start_tile_y\":%d,"
      "\"observed_rng_seed\":%u,\"map_sha1\":\"%s\"}\n",
      run_id.c_str(), variant.c_str(), full ? "true" : "false",
      BWAPI::Broodwar->isReplay() ? "true" : "false",
      player_id, start.x, start.y, rng_seed, map_sha1.c_str());
  }
  void onFrame() override {
    if (match_active && !match_started && !BWAPI::Broodwar->isPaused() &&
        BWAPI::Broodwar->getFrameCount() >= 1)
      match_started = write_match_marker(
          match_ready_path, "ready", BWAPI::Broodwar->getFrameCount());
    if (render_active && !render_started && !BWAPI::Broodwar->isPaused() &&
        BWAPI::Broodwar->getFrameCount() >= 1)
      render_started = write_render_marker(
          render_ready_path, "ready", BWAPI::Broodwar->getFrameCount());
    if (!output || truncated || BWAPI::Broodwar->isPaused()) return;
    const int frame = BWAPI::Broodwar->getFrameCount();
    const bool full = BWAPI::Broodwar->isFlagEnabled(BWAPI::Flag::CompleteMapInformation);
    int emitted = 0;
    int dropped = 0;
    const int main_ai_event_ms = BWAPI::Broodwar->getLastEventTime();
    const int logical_fps = BWAPI::Broodwar->getFPS();
    std::fprintf(output,
      "{\"schema\":1,\"event\":\"frame\",\"frame\":%d,"
      "\"complete_information\":%s,\"main_ai_event_ms\":%d,"
      "\"logical_fps\":%d,\"units\":[",
      frame, full ? "true" : "false", main_ai_event_ms, logical_fps);
    for (BWAPI::Unit u : BWAPI::Broodwar->getAllUnits()) {
      if (!u || !u->exists()) continue;
      if (!tracked(u)) continue;
      if (emitted >= MAX_UNITS_PER_FRAME) { ++dropped; continue; }
      const BWAPI::Position p = u->getPosition();
      // No order issued to the game. BWAPI getters are called in onFrame only.
      std::fprintf(output,
        "%s{\"id\":%d,\"player\":%d,\"type\":%d,\"x\":%d,\"y\":%d,"
        "\"vx\":%.5f,\"vy\":%.5f,\"hp\":%d,\"shields\":%d,"
        "\"air_cooldown\":%d,\"attack_start\":%s,\"attack_frame\":%s,"
        "\"attacking\":%s,\"spores\":%d,\"target_id\":%d}",
        emitted ? "," : "", u->getID(), unit_owner(u), u->getType().getID(),
        p.x, p.y, u->getVelocityX(), u->getVelocityY(),
        u->getHitPoints(), u->getShields(), u->getAirWeaponCooldown(),
        u->isStartingAttack() ? "true" : "false",
        u->isAttackFrame() ? "true" : "false",
        u->isAttacking() ? "true" : "false",
        u->getAcidSporeCount(), target_id(u));
      ++emitted;
    }
    std::fprintf(output, "],\"dropped_units\":%d}\n", dropped);
    ++frames;
    if (frames % 128 == 0) {
      std::fflush(output);
      // Once capped, record a visible failure. It is never silently
      // treated as a complete gameplay trace by the analyzer.
      const long size = std::ftell(output);
      if (size < 0 || size >= MAX_OUTPUT_BYTES) truncated = true;
    }
  }
  void onEnd(bool winner_for_current_player) override {
    if (match_active && match_started && !BWAPI::Broodwar->isReplay())
      write_match_marker(match_done_path, "done", BWAPI::Broodwar->getFrameCount());
    if (render_active && render_started && BWAPI::Broodwar->isReplay())
      write_render_marker(render_done_path, "done", BWAPI::Broodwar->getFrameCount());
    if (!output) return;
    std::fprintf(output,
      "{\"schema\":1,\"event\":\"end\",\"frame\":%d,"
      "\"observed_frames\":%lu,\"truncated\":%s,\"win_reported_for_current_player\":%s}\n",
      BWAPI::Broodwar->getFrameCount(), frames,
      truncated ? "true" : "false", winner_for_current_player ? "true" : "false");
    close_output();
  }
  ~TraceTournamentAI() override { close_output(); }
};

// Replay mode does not need Pluto's inference engine. Export a no-op replay AI
// from this same locally built DLL, and load it as both ai= and tournament=
// in the private GUI playback installation. Actual live matches keep Pluto.
class ReplayNoopAI final : public BWAPI::AIModule {
 public:
  void onFrame() override {}
};

// Preserve the active bot's permissions and behavior. This tournament
// module is a passive monitor, rather than a referee or rule enforcer.
class TraceTournamentPolicy final : public BWAPI::TournamentModule {
 public:
  bool onAction(BWAPI::Tournament::ActionID, void* = nullptr) override {
    return true;
  }
  void onFirstAdvertisement() override {}
};

extern "C" __declspec(dllexport)
void gameInit(BWAPI::Game* game) { BWAPI::BroodwarPtr = game; }

extern "C" __declspec(dllexport)
BWAPI::AIModule* newTournamentAI() { return new TraceTournamentAI(); }

extern "C" __declspec(dllexport)
BWAPI::AIModule* newAIModule() { return new ReplayNoopAI(); }

extern "C" __declspec(dllexport)
BWAPI::TournamentModule* newTournamentModule() { return new TraceTournamentPolicy(); }
