#include "netplay/session.h"

#include <enet/enet.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <condition_variable>
#include <cstdlib>
#include <deque>
#include <map>
#include <mutex>
#include <thread>

namespace giantsrecomp::netplay {
namespace {
constexpr uint32_t kProtocol = 1;
constexpr size_t kMaxPending = 128;
enum Message : uint8_t { kHello = 1, kStartup, kInput, kReject };

void Put32(std::vector<uint8_t>& bytes, uint32_t n) {
  for (int i = 0; i < 4; ++i) bytes.push_back(uint8_t(n >> (8 * i)));
}

bool Get32(std::span<const uint8_t>& bytes, uint32_t& n) {
  if (bytes.size() < 4) return false;
  n = 0;
  for (int i = 0; i < 4; ++i) n |= uint32_t(bytes[i]) << (8 * i);
  bytes = bytes.subspan(4);
  return true;
}

void PutString(std::vector<uint8_t>& bytes, const std::string& s) {
  Put32(bytes, uint32_t(s.size()));
  bytes.insert(bytes.end(), s.begin(), s.end());
}

bool GetString(std::span<const uint8_t>& bytes, std::string& s, size_t max) {
  uint32_t n;
  if (!Get32(bytes, n) || n > max || n > bytes.size()) return false;
  s.assign(reinterpret_cast<const char*>(bytes.data()), n);
  bytes = bytes.subspan(n);
  return true;
}

bool HashValid(const std::string& hash) {
  return hash.size() == 64 && std::all_of(hash.begin(), hash.end(), [](char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
  });
}

bool InitEnet() {
  static const bool ok = [] {
    if (enet_initialize() != 0) return false;
    std::atexit(enet_deinitialize);
    return true;
  }();
  return ok;
}
}  // namespace

bool ParseEndpoint(const std::string& endpoint, std::string& address, uint16_t& port) {
  const auto colon = endpoint.find(':');
  if (colon == std::string::npos || colon == 0) return false;
  uint32_t number = 0;
  const auto tail = endpoint.substr(colon + 1);
  auto [end, ec] = std::from_chars(tail.data(), tail.data() + tail.size(), number);
  if (ec != std::errc() || end != tail.data() + tail.size() || number == 0 || number > 65535)
    return false;
  ENetAddress parsed{};
  const auto ip = endpoint.substr(0, colon);
  if (enet_address_set_host_ip(&parsed, ip.c_str()) != 0 || ip == "0.0.0.0") return false;
  address = ip;
  port = uint16_t(number);
  return true;
}

struct Session::Impl {
  mutable std::mutex mutex;
  std::mutex stop_mutex;
  std::condition_variable cv;
  std::thread worker;
  SessionOptions options;
  SessionIdentity identity, remote;
  SessionStatus status;
  std::vector<uint8_t> startup;
  std::deque<std::vector<uint8_t>> outgoing;
  std::map<uint32_t, std::vector<uint8_t>> incoming;
  bool stop = false;
  bool hello = false;
  bool got_startup = false;
  uint32_t next_send = 0;
  uint32_t next_incoming = 0;
  uint32_t next_receive = 0;

  void Fail(const std::string& error) {  // mutex held
    if (status.error.empty()) status.error = error;
    status.ready = false;
    cv.notify_all();
  }

  bool Packet(ENetPeer* peer, const std::vector<uint8_t>& bytes) {
    auto* packet = enet_packet_create(bytes.data(), bytes.size(), ENET_PACKET_FLAG_RELIABLE);
    if (!packet) return false;
    if (enet_peer_send(peer, 0, packet) == 0) return true;
    enet_packet_destroy(packet);
    return false;
  }

  bool Handle(ENetPeer* peer, std::span<const uint8_t> bytes, std::string& error) {
    if (bytes.empty()) { error = "Empty netplay message"; return false; }
    const auto type = bytes.front();
    bytes = bytes.subspan(1);
    if (type == kReject) {
      error = "Peer rejected session";
      std::string reason;
      if (GetString(bytes, reason, 256) && bytes.empty()) error += ": " + reason;
      return false;
    }
    if (type == kHello) {
      uint32_t protocol, role, delay;
      if (hello || !Get32(bytes, protocol) || !Get32(bytes, role) || !Get32(bytes, delay) ||
          !GetString(bytes, remote.xex_hash, 64) || !GetString(bytes, remote.build_hash, 64) ||
          !GetString(bytes, remote.settings, 1024) || !bytes.empty()) {
        error = "Malformed or repeated netplay handshake";
        return false;
      }
      if (protocol != kProtocol) error = "Netplay protocol mismatch";
      else if (role != uint32_t(!options.host)) error = "Netplay role mismatch";
      else if (remote.xex_hash != identity.xex_hash) error = "Game executable mismatch";
      else if (remote.build_hash != identity.build_hash) error = "Netplay build mismatch";
      else if (delay > 12) error = "Invalid input delay";
      if (!error.empty()) return false;
      hello = true;
      status.delay = options.host ? options.delay : delay;
      next_send = next_incoming = next_receive = status.delay;
      if (options.host) {
        std::vector<uint8_t> message{kStartup};
        message.insert(message.end(), startup.begin(), startup.end());
        if (!Packet(peer, message)) { error = "Could not send save snapshot"; return false; }
        // Client acknowledges receipt before either side consumes guest inputs.
      }
      return true;
    }
    if (type == kStartup) {
      if (!hello || got_startup || bytes.size() > kMaxStartupBytes ||
          (options.host && !bytes.empty())) {
        error = "Unexpected or oversized save snapshot";
        return false;
      }
      got_startup = true;
      if (!options.host) {
        startup.assign(bytes.begin(), bytes.end());
        if (!Packet(peer, {kStartup})) { error = "Could not acknowledge startup"; return false; }
      }
      status.ready = true;
      cv.notify_all();
      return true;
    }
    if (type == kInput) {
      uint32_t index;
      if (!status.ready || !Get32(bytes, index) || bytes.size() > kMaxInputBytes ||
          index != next_incoming || incoming.size() >= kMaxPending || index == UINT32_MAX) {
        error = "Invalid, out-of-order or excessive input messages";
        return false;
      }
      ++next_incoming;
      incoming.emplace(index, std::vector<uint8_t>(bytes.begin(), bytes.end()));
      cv.notify_all();
      return true;
    }
    error = "Unknown netplay message";
    return false;
  }

  void Run() {
    ENetAddress address{};
    address.host = ENET_HOST_ANY;
    address.port = options.port;
    ENetHost* host = enet_host_create(options.host ? &address : nullptr, 1, 1, 0, 0);
    if (!host) {
      std::lock_guard lock(mutex);
      Fail("Could not create netplay socket (port may be in use)");
      return;
    }
    host->maximumPacketSize = kMaxStartupBytes + 1;
    host->maximumWaitingData = 2 * (kMaxStartupBytes + 1);
    {
      std::lock_guard lock(mutex);
      status.port = host->address.port;
      cv.notify_all();
    }
    ENetPeer* peer = nullptr;
    if (!options.host) {
      if (enet_address_set_host_ip(&address, options.address.c_str()) == 0) {
        address.port = options.port;
        peer = enet_host_connect(host, &address, 1, kProtocol);
      }
      if (!peer) {
        std::lock_guard lock(mutex);
        Fail("Could not connect to numeric IPv4 endpoint");
      }
    }
    const auto deadline = std::chrono::steady_clock::now() + options.timeout;
    for (;;) {
      {
        std::lock_guard lock(mutex);
        if (stop || !status.error.empty()) break;
        if (!status.ready && std::chrono::steady_clock::now() >= deadline) {
          Fail("Timed out waiting for netplay handshake");
          break;
        }
        while (peer && !outgoing.empty()) {
          if (!Packet(peer, outgoing.front())) { Fail("Could not send input"); break; }
          outgoing.pop_front();
        }
        if (peer) status.ping_ms = peer->roundTripTime;
      }
      enet_host_flush(host);
      ENetEvent event{};
      const int service = enet_host_service(host, &event, 2);
      std::lock_guard lock(mutex);
      if (service < 0) { Fail("Netplay socket service failed"); break; }
      if (service == 0) continue;
      if (event.type == ENET_EVENT_TYPE_CONNECT) {
        peer = event.peer;
        enet_peer_timeout(peer, 8, 3000, 10000);
        std::vector<uint8_t> message{kHello};
        Put32(message, kProtocol);
        Put32(message, uint32_t(options.host));
        Put32(message, options.delay);
        PutString(message, identity.xex_hash);
        PutString(message, identity.build_hash);
        PutString(message, identity.settings);
        if (!Packet(peer, message)) Fail("Could not send handshake");
      } else if (event.type == ENET_EVENT_TYPE_DISCONNECT) {
        Fail("Netplay peer disconnected");
      } else if (event.type == ENET_EVENT_TYPE_RECEIVE) {
        std::string error;
        if (event.channelID != 0 || !Handle(peer,
              {event.packet->data, event.packet->dataLength}, error)) {
          if (error.empty()) error = "Invalid netplay channel";
          std::vector<uint8_t> reject{kReject};
          PutString(reject, error);
          Packet(peer, reject);
          enet_host_flush(host);
          Fail(error);
        }
        enet_packet_destroy(event.packet);
      }
    }
    if (peer) {
      enet_peer_disconnect_now(peer, 0);
      enet_host_flush(host);
    }
    enet_host_destroy(host);
  }
};

Session::Session() : impl_(std::make_unique<Impl>()) {}
Session::~Session() { Stop(); }

bool Session::Start(const SessionOptions& options, const SessionIdentity& identity,
                    std::vector<uint8_t> startup, std::string& error) {
  std::lock_guard lock(impl_->mutex);
  if (impl_->stop) { error = "Session was cancelled"; return false; }
  if (impl_->worker.joinable() || !HashValid(identity.xex_hash) ||
      !HashValid(identity.build_hash) || identity.settings.size() > 1024 || options.delay > 12 ||
      options.timeout.count() <= 0 || startup.size() > kMaxStartupBytes ||
      (!options.host && (!startup.empty() || options.port == 0))) {
    error = "Invalid session options or identity";
    return false;
  }
  if (!InitEnet()) { error = "ENet initialization failed"; return false; }
  impl_->options = options;
  impl_->identity = identity;
  impl_->startup = std::move(startup);
  impl_->status.delay = options.delay;
  impl_->worker = std::thread([this] { impl_->Run(); });
  return true;
}

bool Session::WaitReady(std::string& error) {
  std::unique_lock lock(impl_->mutex);
  if (impl_->stop) { error = "Session was cancelled"; return false; }
  if (!impl_->worker.joinable()) { error = "Session has not started"; return false; }
  impl_->cv.wait_for(lock, impl_->options.timeout, [&] {
    return impl_->status.ready || impl_->stop || !impl_->status.error.empty();
  });
  if (impl_->status.ready && !impl_->stop) return true;
  if (impl_->status.error.empty()) impl_->Fail("Netplay startup cancelled or timed out");
  error = impl_->status.error;
  return false;
}

bool Session::Send(uint32_t index, std::span<const uint8_t> payload, std::string& error) {
  std::lock_guard lock(impl_->mutex);
  if (!impl_->status.ready || impl_->stop || !impl_->status.error.empty()) {
    error = impl_->status.error.empty() ? "Session is not running" : impl_->status.error;
    return false;
  }
  if (payload.size() > kMaxInputBytes || impl_->outgoing.size() >= kMaxPending ||
      index != impl_->next_send || index == UINT32_MAX) {
    impl_->Fail("Invalid or excessive outgoing input");
    error = impl_->status.error;
    return false;
  }
  std::vector<uint8_t> message{kInput};
  Put32(message, index);
  message.insert(message.end(), payload.begin(), payload.end());
  impl_->outgoing.push_back(std::move(message));
  ++impl_->next_send;
  return true;
}

bool Session::Receive(uint32_t index, std::vector<uint8_t>& payload, std::string& error) {
  std::unique_lock lock(impl_->mutex);
  if (index != impl_->next_receive) { error = "Unexpected input read index"; return false; }
  impl_->cv.wait_for(lock, impl_->options.timeout, [&] {
    return impl_->incoming.contains(index) || impl_->stop || !impl_->status.error.empty();
  });
  if (impl_->stop || !impl_->status.error.empty() || !impl_->incoming.contains(index)) {
    if (impl_->status.error.empty()) impl_->Fail("Netplay input cancelled or timed out");
    error = impl_->status.error;
    return false;
  }
  payload = std::move(impl_->incoming.at(index));
  impl_->incoming.erase(index);
  ++impl_->next_receive;
  return true;
}

void Session::Stop() {
  std::lock_guard stop_lock(impl_->stop_mutex);
  {
    std::lock_guard lock(impl_->mutex);
    impl_->stop = true;
    impl_->status.ready = false;
    impl_->cv.notify_all();
  }
  if (impl_->worker.joinable()) impl_->worker.join();
}
SessionStatus Session::status() const {
  std::lock_guard lock(impl_->mutex);
  return impl_->status;
}
SessionIdentity Session::remote_identity() const {
  std::lock_guard lock(impl_->mutex);
  return impl_->remote;
}
std::vector<uint8_t> Session::startup() const {
  std::lock_guard lock(impl_->mutex);
  return impl_->startup;
}
}  // namespace giantsrecomp::netplay
