#pragma once

#include <chrono>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace giantsrecomp::netplay {

constexpr size_t kMaxInputBytes = 32 * 1024;
constexpr size_t kMaxStartupBytes = 16 * 1024 * 1024 + kMaxInputBytes + 4;

struct SessionOptions {
  bool host = false;
  std::string address = "127.0.0.1";
  uint16_t port = 24872;
  uint32_t delay = 3;
  std::chrono::milliseconds timeout{30000};
  // Relay: the client's port is 0 (an ephemeral socket); the host keeps its netplay port.
  bool relay = false;
  std::string lobby_address;  // One or more "host[:port]" candidates, separated by ';'.
  std::string room;
};

struct SessionIdentity {
  std::string xex_hash;
  std::string build_hash;
  std::string settings;  // Host-owned gameplay settings, validated by the app before launch.
};

struct SessionStatus {
  bool ready = false;
  uint16_t port = 0;
  uint32_t ping_ms = 0;
  uint32_t delay = 0;
  std::string error;
  bool relay = false;
  std::string room;
  std::string relay_endpoint;
  std::string lobby_state;
};

// ENet is serviced by one host thread. Guest input waits retain the deterministic run token:
// the network must never decide which guest thread runs next.
class Session {
 public:
  Session();
  ~Session();
  Session(const Session&) = delete;
  Session& operator=(const Session&) = delete;

  bool Start(const SessionOptions& options, const SessionIdentity& identity,
             std::vector<uint8_t> host_startup, std::string& error);
  bool WaitReady(std::string& error);
  bool Send(uint32_t index, std::span<const uint8_t> payload, std::string& error);
  bool Receive(uint32_t index, std::vector<uint8_t>& payload, std::string& error);
  void Stop();
  SessionStatus status() const;
  SessionIdentity remote_identity() const;
  std::vector<uint8_t> startup() const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

// Numeric IPv4 only; no DNS lookup on the UI thread and no ambiguous port parsing.
bool ParseEndpoint(const std::string& endpoint, std::string& address, uint16_t& port);

}  // namespace giantsrecomp::netplay
