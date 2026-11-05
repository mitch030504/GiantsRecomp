#include "portal_ipc.h"

#include "portal_ipc_request.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <functional>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <thread>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
using socket_t = SOCKET;
constexpr socket_t kInvalidSocket = INVALID_SOCKET;
inline int CloseSocket(socket_t s) { return closesocket(s); }
#else
#include <arpa/inet.h>
#include <cerrno>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
using socket_t = int;
constexpr socket_t kInvalidSocket = -1;
inline int CloseSocket(socket_t s) { return close(s); }
#endif

#include <rex/cvar.h>
#include <rex/input/input_system.h>
#include <rex/logging.h>

#include "portal/figure_crypto.h"
#include "portal/figure_file.h"
#include "portal/portal_device.h"
#include "portal/software/software_portal.h"
#include "portal/usb/usb_portal.h"
#include "portal_rex/portal_rex.h"

REXCVAR_DEFINE_BOOL(portal_ipc, true, "Portal",
    "Serve the Omoio/Cemu-compatible portal IPC on 127.0.0.1 (localhost only) so an external "
    "portal menu (skyportal/Omoio) can place, remove and create figures.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_INT32(portal_ipc_port, 28014, "Portal", "TCP port for the portal IPC server.")
    .range(1, 65535).lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace giantsrecomp::portal_ipc {
namespace {
namespace fs = std::filesystem;
using rex::X_RESULT;
using skylanders::portal::FigureData;

constexpr int kMaxSlots = skylanders::portal::kMaxFigures;

// One connection may live this long in total, however slowly the client dribbles bytes. Before
// this the only bound was a 2 s receive timeout per byte, so a client sending one byte just inside
// each timeout held the one accept loop for as long as it liked (slowloris) -- and the timeout
// executed whatever partial line it had accumulated.
constexpr auto kConnectionDeadline = std::chrono::seconds(10);
// An idle connection wakes up this often so the absolute deadline above is re-checked even when
// every byte arrives just inside the receive timeout.
constexpr auto kRecvPoll = std::chrono::milliseconds(250);
static_assert(kRecvPoll < kConnectionDeadline,
              "the receive poll must fire before the connection deadline");

std::atomic<bool> g_hushed{false};
std::function<bool()> g_hush_gate = [] { return true; };

// One server thread, so plain bools are enough. A broken or hostile client must cost one
// connection, not the log: each failure below is reported once and then stays quiet.
bool g_warned_incomplete = false;
bool g_warned_send = false;
bool g_warned_accept = false;
bool g_warned_failure = false;

// One command per connection: read one terminated line (until \n or \r, max 4096 bytes), reply,
// close. A line that never terminates inside the connection deadline is refused, never executed.
// The reply's last line starts with "ok" or "error", matching Cemu's SkylanderPortalIPC.
std::string ProcessCommand(const std::string& line) {
  const auto first_space = line.find(' ');
  const std::string command = first_space == std::string::npos ? line : line.substr(0, first_space);
  const std::string arguments = first_space == std::string::npos ? "" : line.substr(first_space + 1);

  if (command == "hello") return "ok giantsrecomp-portal 1\n";
  if (command == "status") {
    std::string reply;
    auto* software = skylanders::GetSoftwarePortal();
    auto* usb = skylanders::GetUsbPortal();
    for (int slot = 0; slot < kMaxSlots; ++slot) {
      std::optional<std::pair<uint16_t, uint16_t>> figure;
      if (software) {
        if (auto data = software->Figure(slot)) {
          figure = {skylanders::portal::ReadFigureId(*data), skylanders::portal::ReadFigureVariant(*data)};
        }
      } else if (usb) {
        figure = usb->DetectedIdVariant(slot);
      }
      if (figure) {
        reply += "slot" + std::to_string(slot) + " loaded " + std::to_string(figure->first) + " " +
                 std::to_string(figure->second) + "\n";
      } else {
        reply += "slot" + std::to_string(slot) + " empty\n";
      }
    }
    return reply + "ok\n";
  }
  if (command == "load") {
    const auto space = arguments.find(' ');
    const std::string slot_text = space == std::string::npos ? arguments : arguments.substr(0, space);
    const std::string path_text = space == std::string::npos ? "" : arguments.substr(space + 1);
    if (slot_text.empty()) return "error missing arguments\n";
    if (path_text.empty()) return "error missing path\n";
    int slot = 0;
    try {
      slot = std::stoi(slot_text);
    } catch (const std::exception&) {
      return "error invalid arguments\n";
    }
    if (slot < -1 || slot >= kMaxSlots) return "error invalid slot\n";
    const fs::path path = fs::path(std::u8string(
        reinterpret_cast<const char8_t*>(path_text.data()), path_text.size()));
    if (!path.is_absolute()) return "error path must be absolute\n";
    std::error_code ec;
    if (!fs::is_regular_file(path, ec)) return "error cannot open file\n";
    if (fs::file_size(path, ec) != skylanders::portal::kFigureSize || ec) {
      return "error not a skylander file\n";
    }
    auto data = skylanders::portal::LoadFigureFile(path);
    if (!data) return "error cannot open file\n";
    const uint16_t stored_crc = uint16_t((*data)[0x1E] | (uint16_t((*data)[0x1F]) << 8));
    if (stored_crc != skylanders::portal::FigureCrc16(data->data(), 0x1E)) {
      return "error not a skylander file\n";
    }
    auto* software = skylanders::GetSoftwarePortal();
    if (!software) return "error no software portal\n";
    if (slot == -1) {
      slot = -1;
      for (int i = 0; i < kMaxSlots; ++i) {
        if (!software->HasFigure(i)) { slot = i; break; }
      }
      if (slot == -1) return "error no free slot\n";
    } else if (software->HasFigure(slot)) {
      return "error no free slot\n";
    }
    if (!skylanders::PlaceFigureFromFile(slot, path)) return "error cannot place figure\n";
    return "ok " + std::to_string(slot) + "\n";
  }
  if (command == "remove") {
    if (arguments.empty()) return "error missing slot\n";
    int slot = 0;
    try {
      slot = std::stoi(arguments);
    } catch (const std::exception&) {
      return "error invalid slot\n";
    }
    if (slot < 0 || slot >= kMaxSlots) return "error invalid slot\n";
    auto* software = skylanders::GetSoftwarePortal();
    if (!software) return "error no software portal\n";
    if (!software->HasFigure(slot)) return "error slot empty\n";
    if (!skylanders::RemoveFigureFromSlot(slot)) return "error cannot remove figure\n";
    return "ok\n";
  }
  if (command == "clear") {
    auto* software = skylanders::GetSoftwarePortal();
    if (!software) return "error no software portal\n";
    for (int slot = 0; slot < kMaxSlots; ++slot) skylanders::RemoveFigureFromSlot(slot);
    return "ok\n";
  }
  if (command == "create") {
    // id variant path -- the path is everything after the second space, so it may contain spaces.
    const auto first = arguments.find(' ');
    if (first == std::string::npos) return "error missing path\n";
    const auto second = arguments.find(' ', first + 1);
    if (second == std::string::npos) return "error missing path\n";
    const std::string id_text = arguments.substr(0, first);
    const std::string variant_text = arguments.substr(first + 1, second - first - 1);
    const std::string path_text = arguments.substr(second + 1);
    long id = 0, variant = 0;
    try {
      id = std::stol(id_text);
      variant = std::stol(variant_text);
    } catch (const std::exception&) {
      return "error invalid id\n";
    }
    if (id < 0 || id > 0xFFFF) return "error invalid id\n";
    if (variant < 0 || variant > 0xFFFF) return "error invalid variant\n";
    const fs::path path = fs::path(std::u8string(
        reinterpret_cast<const char8_t*>(path_text.data()), path_text.size()));
    if (!path.is_absolute()) return "error path must be absolute\n";
    std::error_code ec;
    if (fs::exists(path, ec)) return "error file exists\n";
    const FigureData data = skylanders::portal::CreateBlankFigure(uint16_t(id), uint16_t(variant));
    // SaveFigureFileAtomic now creates an unpredictably named sibling with exclusive-create
    // semantics, so a symlink planted next to the destination cannot be followed. This is the only
    // create path reachable over IPC.
    if (!skylanders::portal::SaveFigureFileAtomic(path, data)) return "error cannot create file\n";
    return "ok\n";
  }
  if (command == "hush") {
    if (arguments == "on") g_hushed.store(true);
    else if (arguments == "off") g_hushed.store(false);
    else return "error invalid argument\n";
    return "ok\n";
  }
  return "error unknown command\n";
}

// The receive timeout is a poll period, not the request budget: the absolute deadline in
// ReadRequest() ends a connection, so a slow client cannot buy more time with every byte.
void ConfigureConnection(socket_t conn) {
#ifdef _WIN32
  DWORD timeout = static_cast<DWORD>(kRecvPoll.count());
  setsockopt(conn, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
#else
  timeval timeout{0, static_cast<suseconds_t>(kRecvPoll.count() * 1000)};
  setsockopt(conn, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
#endif
}

// Reads at most one request line and never past `deadline`. A timeout, an early close or an
// oversized line leaves the status short of kComplete, and HandleConnection() then refuses to run
// whatever was assembled: the old reader returned the partial line and it was executed.
RequestLine ReadRequest(socket_t conn, std::chrono::steady_clock::time_point deadline) {
  RequestLine request;
  char byte = 0;
  for (;;) {
    if (std::chrono::steady_clock::now() >= deadline) return request;
    const auto received = recv(conn, &byte, 1, 0);
    if (received == 0) return request;  // the client closed before ending its line
    if (received < 0) {
#ifdef _WIN32
      const int error = WSAGetLastError();
      if (error == WSAEINTR) continue;                                 // a signal is not a request
      if (error == WSAETIMEDOUT || error == WSAEWOULDBLOCK) continue;  // poll; the deadline decides
      return request;
#else
      if (errno == EINTR) continue;                           // a signal is not a request
      if (errno == EAGAIN || errno == EWOULDBLOCK) continue;  // poll; the deadline decides
      return request;
#endif
    }
    if (request.Feed(byte) != RequestLine::Status::kWaiting) return request;
  }
}

// Writes the whole reply. The send() result used to be discarded, so a short write silently
// truncated the answer -- send() is allowed to accept only part of a buffer -- and the client
// treated the partial answer as complete. A hard error means the peer is gone, so the caller
// just closes.
bool SendAll(socket_t conn, std::string_view reply) {
  std::size_t sent = 0;
  while (sent < reply.size()) {
    const std::size_t remaining = reply.size() - sent;
#ifdef _WIN32
    // Winsock takes an int length; replies are small, but clamp instead of narrowing a size_t.
    const int chunk =
        static_cast<int>(std::min(remaining, std::size_t(std::numeric_limits<int>::max())));
    const auto written = send(conn, reply.data() + sent, chunk, 0);
    if (written == SOCKET_ERROR) {
      if (WSAGetLastError() == WSAEINTR) continue;
      return false;
    }
#else
#if defined(MSG_NOSIGNAL)
    // Without MSG_NOSIGNAL a peer that reset the connection makes send() raise SIGPIPE, whose
    // default action kills the whole process; with it the same case is the EPIPE below.
    const auto written = send(conn, reply.data() + sent, remaining, MSG_NOSIGNAL);
#else
    const auto written = send(conn, reply.data() + sent, remaining, 0);  // Start() ignores SIGPIPE
#endif
    if (written < 0) {
      if (errno == EINTR) continue;
      return false;
    }
#endif
    if (written == 0) return false;  // the peer stopped taking bytes
    sent += static_cast<std::size_t>(written);
  }
  return true;
}

void HandleConnection(socket_t conn) {
  // Never throws: this runs on a detached thread, where an escaping exception (only an allocation
  // failure is realistic here) would terminate the game. A failed request costs one connection.
  try {
    const auto deadline = std::chrono::steady_clock::now() + kConnectionDeadline;
    const RequestLine request = ReadRequest(conn, deadline);
    std::string reply;
    if (Executable(request.status())) {
      reply = ProcessCommand(request.Text());
    } else {
      // Discard it. Executing what arrived is how a timeout ran a truncated command (a cut-off
      // `create` path, or `hush of`); the client can see the refusal and resend the whole line.
      reply = "error incomplete request\n";
      if (!g_warned_incomplete) {
        g_warned_incomplete = true;
        REXLOG_WARN("Portal IPC: dropped a {} request ({} bytes) instead of running it",
                    request.status() == RequestLine::Status::kOversized ? "oversized" : "partial",
                    request.Text().size());
      }
    }
    if (!SendAll(conn, reply) && !g_warned_send) {
      g_warned_send = true;
      REXLOG_WARN("Portal IPC: could not send a reply; the client had already closed");
    }
  } catch (...) {
    if (!g_warned_failure) {
      g_warned_failure = true;
      REXLOG_WARN("Portal IPC: a request failed; dropping the connection");
    }
  }
}

void ServeLoop(uint16_t port) {
#ifdef _WIN32
  WSADATA wsa{};
  if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
    REXLOG_ERROR("Portal IPC: WSAStartup failed");
    return;
  }
#endif
  socket_t listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (listener == kInvalidSocket) {
    REXLOG_ERROR("Portal IPC: cannot create socket");
    return;
  }
  int reuse = 1;
  setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);  // localhost only: the protocol has no auth
  address.sin_port = htons(port);
  if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
      listen(listener, 8) != 0) {
    REXLOG_WARN("Portal IPC: cannot listen on 127.0.0.1:{} (is another portal server running?)",
                port);
    CloseSocket(listener);
    return;
  }
  REXLOG_INFO("Portal IPC: serving the Omoio/Cemu portal protocol on 127.0.0.1:{}", port);
  for (;;) {
    socket_t conn = accept(listener, nullptr, nullptr);
    if (conn == kInvalidSocket) {
      // accept() fails transiently (EINTR, ECONNABORTED) or because descriptors ran out. Report it
      // once, back off so a persistent failure cannot spin, and keep listening for other clients.
      if (!g_warned_accept) {
        g_warned_accept = true;
        REXLOG_WARN("Portal IPC: accept failed; still listening on 127.0.0.1:{}", port);
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      continue;
    }
    ConfigureConnection(conn);
    HandleConnection(conn);
    CloseSocket(conn);
  }
}

std::atomic<bool> g_started{false};
}  // namespace

void Start() {
  bool expected = false;
  if (!g_started.compare_exchange_strong(expected, true)) return;
  if (!REXCVAR_GET(portal_ipc)) return;
#if defined(SIGPIPE)
  // A client that resets the connection while a reply is being sent makes send() raise SIGPIPE,
  // and its default action kills the process -- one local RST took the game down. MSG_NOSIGNAL
  // covers the reply path itself; ignoring the signal also covers a platform without it.
  std::signal(SIGPIPE, SIG_IGN);
#endif
  const uint16_t port = uint16_t(REXCVAR_GET(portal_ipc_port));
  // Detached, like the portal itself: the server lives for the whole process.
  std::thread(ServeLoop, port).detach();
}

void SetHushGate(std::function<bool()> gate) { g_hush_gate = std::move(gate); }

bool Hushed() { return g_hushed.load(); }

void InstallHushFilter(rex::input::InputSystem* input) {
  if (!input) return;
  input->AddStateFilter([](uint32_t, rex::X_RESULT result, rex::input::X_INPUT_STATE* state) {
    if (g_hushed.load() && g_hush_gate()) {
      *state = {};
      return X_ERROR_SUCCESS;  // a connected, neutral pad
    }
    return result;
  });
  input->AddKeystrokeFilter([](uint32_t, rex::X_RESULT result, rex::input::X_INPUT_KEYSTROKE* keystroke) {
    if (g_hushed.load() && g_hush_gate()) {
      *keystroke = {};
      return X_ERROR_EMPTY;
    }
    return result;
  });
}

}  // namespace giantsrecomp::portal_ipc
