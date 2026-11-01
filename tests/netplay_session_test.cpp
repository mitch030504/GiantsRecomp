#include "netplay/session.h"

#include <enet/enet.h>

#include <array>
#include <chrono>
#include <cstdio>
#include <future>
#include <thread>

using namespace giantsrecomp::netplay;
using namespace std::chrono_literals;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

static uint16_t BoundPort(Session& host) {
  for (int i = 0; i < 500; ++i) {
    auto status = host.status();
    if (status.port || !status.error.empty()) return status.port;
    std::this_thread::sleep_for(2ms);
  }
  return 0;
}
static SessionIdentity Identity(char build = 'a') {
  return {std::string(64, '1'), std::string(64, build), "language=1"};
}

static void Put32(std::vector<uint8_t>& bytes, uint32_t n) {
  for (int i = 0; i < 4; ++i) bytes.push_back(uint8_t(n >> (8 * i)));
}
static void PutString(std::vector<uint8_t>& bytes, const std::string& text) {
  Put32(bytes, uint32_t(text.size()));
  bytes.insert(bytes.end(), text.begin(), text.end());
}
static std::vector<uint8_t> Hello(uint32_t protocol = 1) {
  std::vector<uint8_t> bytes{1};
  Put32(bytes, protocol);
  Put32(bytes, 0);  // Client role.
  Put32(bytes, 3);
  PutString(bytes, Identity().xex_hash);
  PutString(bytes, Identity().build_hash);
  PutString(bytes, "");
  return bytes;
}
static bool SendRaw(ENetHost* host, ENetPeer* peer, const std::vector<uint8_t>& bytes) {
  ENetPacket* packet = enet_packet_create(bytes.data(), bytes.size(), ENET_PACKET_FLAG_RELIABLE);
  if (!packet) return false;
  if (enet_peer_send(peer, 0, packet) != 0) {
    enet_packet_destroy(packet);
    return false;
  }
  enet_host_flush(host);
  return true;
}

// Exercise parser failures through the actual UDP/ENet boundary, including fragmented inputs.
static void RejectRaw(const std::vector<uint8_t>& bad, const char* expected, bool ready = false) {
  Session host;
  SessionOptions options;
  options.host = true;
  options.port = 0;
  options.timeout = 2s;
  std::string error;
  CHECK(host.Start(options, Identity(), {}, error));
  ENetAddress address{};
  address.port = BoundPort(host);
  CHECK(enet_address_set_host_ip(&address, "127.0.0.1") == 0);
  ENetHost* raw = enet_host_create(nullptr, 1, 1, 0, 0);
  CHECK(raw);
  if (!raw) return;
  ENetPeer* peer = enet_host_connect(raw, &address, 1, 1);
  CHECK(peer);
  if (!peer) { enet_host_destroy(raw); return; }
  bool connected = false, startup = false;
  auto pump = [&] {
    ENetEvent event{};
    const int result = enet_host_service(raw, &event, 2);
    CHECK(result >= 0);
    if (result > 0 && event.type == ENET_EVENT_TYPE_CONNECT) connected = true;
    if (result > 0 && event.type == ENET_EVENT_TYPE_RECEIVE) {
      if (event.packet->dataLength && event.packet->data[0] == 2) startup = true;
      enet_packet_destroy(event.packet);
    }
  };
  auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!connected && std::chrono::steady_clock::now() < deadline) pump();
  CHECK(connected);
  if (ready && connected) {
    CHECK(SendRaw(raw, peer, Hello()));
    while (!startup && std::chrono::steady_clock::now() < deadline) pump();
    CHECK(startup);
    CHECK(SendRaw(raw, peer, {2}));  // Acknowledge the save snapshot.
    while (!host.status().ready && host.status().error.empty() &&
           std::chrono::steady_clock::now() < deadline) pump();
    CHECK(host.status().ready);
  }
  CHECK(SendRaw(raw, peer, bad));
  while (host.status().error.empty() && std::chrono::steady_clock::now() < deadline) pump();
  const auto status = host.status();
  CHECK(!status.ready);
  if (status.error.find(expected) == std::string::npos) {
    std::fprintf(stderr, "Expected '%s', got '%s'\n", expected, status.error.c_str());
    CHECK(false);
  }
  host.Stop();
  enet_host_destroy(raw);
}

int main() {
  // Closing before async startup begins must cancel both startup and future waits.
  {
    Session cancelled;
    cancelled.Stop();
    std::string why;
    CHECK(!cancelled.Start(SessionOptions{}, Identity(), {}, why));
    CHECK(!cancelled.WaitReady(why));
  }
  std::string ip, error;
  uint16_t port = 0;
  CHECK(ParseEndpoint("127.0.0.1:24872", ip, port) && ip == "127.0.0.1" && port == 24872);
  for (const auto* endpoint : {"localhost:10", "127.0.0.1", "0.0.0.0:1", "127.0.0.1:0",
                              "127.0.0.1:65536", "127.0.0.1:-1", "127.0.0.1:1junk", "[::1]:2"})
    CHECK(!ParseEndpoint(endpoint, ip, port));
  {
    Session host, client;
    SessionOptions h;
    h.host = true;
    h.port = 0;
    h.delay = 3;
    h.timeout = 2s;
    const std::vector<uint8_t> snapshot{0, 1, 2, 255};
    CHECK(host.Start(h, Identity(), snapshot, error));
    SessionOptions c;
    c.port = BoundPort(host);
    CHECK(c.port != 0);
    c.timeout = 2s;
    c.delay = 9;  // Host chooses the delay.
    CHECK(client.Start(c, Identity(), {}, error));
    CHECK(host.WaitReady(error));
    CHECK(client.WaitReady(error));
    CHECK(client.startup() == snapshot);
    CHECK(client.status().delay == 3);
    CHECK(client.remote_identity().settings == "language=1");
    // Pipelined inputs retain order and exact bytes, including zero bytes.
    for (uint32_t n = 3; n < 30; ++n) {
      const std::array<uint8_t, 3> a{uint8_t(n), 0, 255}, b{0, uint8_t(n), 17};
      CHECK(host.Send(n, a, error));
      CHECK(client.Send(n, b, error));
    }
    std::vector<uint8_t> input;
    for (uint32_t n = 3; n < 30; ++n) {
      CHECK(host.Receive(n, input, error));
      CHECK((input == std::vector<uint8_t>{0, uint8_t(n), 17}));
      CHECK(client.Receive(n, input, error));
      CHECK((input == std::vector<uint8_t>{uint8_t(n), 0, 255}));
    }
    auto wait = std::async(std::launch::async, [&] {
      std::string why;
      return host.Receive(30, input, why);
    });
    client.Stop();
    CHECK(wait.wait_for(3s) == std::future_status::ready);
    CHECK(!wait.get());
    CHECK(!host.status().error.empty());
  }
  for (bool wrong_game : {false, true}) {
    Session host, client;
    SessionOptions h;
    h.host = true; h.port = 0; h.timeout = 500ms;
    CHECK(host.Start(h, Identity(), {}, error));
    SessionOptions c;
    c.port = BoundPort(host); c.timeout = 500ms;
    auto identity = Identity(wrong_game ? 'a' : 'b');
    if (wrong_game) identity.xex_hash[0] = '2';
    CHECK(client.Start(c, identity, {}, error));
    CHECK(!host.WaitReady(error));
    CHECK(host.status().error.find(wrong_game ? "executable" : "build") != std::string::npos);
    CHECK(!client.WaitReady(error));
  }
  {
    Session host;
    SessionOptions h;
    h.host = true; h.port = 0; h.timeout = 50ms;
    CHECK(host.Start(h, Identity(), {}, error));
    CHECK(!host.WaitReady(error));
    CHECK(!error.empty());
  }
  {
    Session session;
    SessionOptions invalid;
    invalid.delay = 13;
    CHECK(!session.Start(invalid, Identity(), {}, error));
  }
  RejectRaw(Hello(2), "protocol mismatch");
  RejectRaw({1, 1, 0}, "Malformed");
  RejectRaw(Hello(), "repeated", true);
  std::vector<uint8_t> input{3};
  Put32(input, 3);
  input.push_back(0);
  RejectRaw(input, "Invalid");  // Input before hello/startup acknowledgement.
  input = {3};
  Put32(input, 4);  // First input must have the negotiated delay index (3).
  RejectRaw(input, "out-of-order", true);
  input = {3};
  Put32(input, 3);
  input.resize(5 + kMaxInputBytes + 1);
  RejectRaw(input, "excessive", true);
  return failures ? 1 : 0;
}
