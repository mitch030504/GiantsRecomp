#pragma once

#include <chrono>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <enet/enet.h>

namespace giantsrecomp::netplay {

constexpr uint16_t kDefaultLobbyPort = 32200;
constexpr size_t kRoomCodeLength = 8;

// "host[:port]", where host is a hostname or a numeric IPv4 address. Port defaults to 32200.
// Syntax only: no DNS lookup.
bool ParseLobbyEndpoint(const std::string& endpoint, std::string& host, uint16_t& port);

// A semicolon/comma-separated candidate list, tried in order: LAN address first, then the
// external one. An unreachable candidate costs one short timeout before the next.
bool ParseLobbyEndpoints(const std::string& endpoints,
                         std::vector<std::pair<std::string, uint16_t>>& out);

// The server's room format: eight hexadecimal characters. The client normalises to upper case.
bool ValidRoomCode(std::string_view code);

// One session's lobby/relay control. Owned and driven by the ENet worker thread: Begin() resolves
// the lobby, Tick() retries the request and keeps the room alive, and Intercept() is called from
// ENet for raw datagrams on the same thread. No method takes the Session mutex.
class LobbyClient {
 public:
  LobbyClient(bool host, std::string lobby_endpoints, std::string room_code,
              uint16_t gameplay_port, const std::string& build_hash);
  ~LobbyClient();
  LobbyClient(const LobbyClient&) = delete;
  LobbyClient& operator=(const LobbyClient&) = delete;

  bool Begin(ENetHost& host, std::string& error);
  void Tick(ENetHost& host, std::chrono::steady_clock::time_point now);
  // ENet raw-datagram callback. Returns 1 for a consumed control or hello-ack datagram.
  int Intercept(ENetHost& host, ENetEvent* event);
  void OnConnected();
  void Shutdown(ENetHost& host);

  bool failed() const { return failed_; }
  const std::string& error() const { return error_; }
  // Client only: JOIN_ACK received, so the session may call enet_host_connect once.
  bool connect_ready() const { return connect_ready_; }
  const ENetAddress& relay_address() const { return relay_; }
  std::string room() const { return room_; }
  std::string relay_endpoint() const;
  std::string state() const;

 private:
  enum class State { kIdle, kCreating, kJoining, kHello, kConnecting, kReady, kFailed };

  bool SendRequest(ENetHost& host, std::string& error);
  bool SelectEndpoint(size_t index, std::string& error);
  void SendHello(ENetHost& host);
  void SendKeepalive(ENetHost& host);
  void HandleControl(ENetHost& host);
  void Fail(const std::string& message);

  bool host_ = false;
  std::string lobby_endpoints_;
  std::vector<std::pair<std::string, uint16_t>> endpoints_;
  size_t endpoint_index_ = 0;
  std::string room_code_;
  uint16_t gameplay_port_ = 0;
  uint32_t build_id_ = 0;
  ENetAddress control_{};
  ENetAddress relay_{};
  std::string room_;
  State state_ = State::kIdle;
  bool active_ = false;
  bool failed_ = false;
  bool connect_ready_ = false;
  std::string error_;
  uint64_t nonce_ = 0;
  std::chrono::steady_clock::time_point deadline_{};
  std::chrono::steady_clock::time_point next_send_{};
  std::chrono::steady_clock::time_point next_hello_{};
  std::chrono::steady_clock::time_point next_keepalive_{};
};

}  // namespace giantsrecomp::netplay
