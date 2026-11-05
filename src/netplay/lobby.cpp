#include "netplay/lobby.h"

#include <algorithm>
#include <cctype>
#include <charconv>
#include <chrono>
#include <cstring>
#include <random>

namespace giantsrecomp::netplay {
namespace {
using Clock = std::chrono::steady_clock;
constexpr uint8_t kVersion = 1;
constexpr uint16_t kProtocolVersion = 1;
enum Kind : uint8_t {
  kCreate = 1,
  kCreateAck = 2,
  kJoin = 3,
  kJoinAck = 4,
  kKeepalive = 5,
  kKeepaliveAck = 6,
  kHealth = 7,
  kHealthAck = 8,
  kError = 9
};

void Put16(std::vector<uint8_t>& bytes, uint16_t value) {
  bytes.push_back(uint8_t(value >> 8));
  bytes.push_back(uint8_t(value));
}
void Put32(std::vector<uint8_t>& bytes, uint32_t value) {
  for (int i = 3; i >= 0; --i) bytes.push_back(uint8_t(value >> (8 * i)));
}
void Put64(std::vector<uint8_t>& bytes, uint64_t value) {
  for (int i = 7; i >= 0; --i) bytes.push_back(uint8_t(value >> (8 * i)));
}
bool Get16(std::span<const uint8_t>& bytes, uint16_t& value) {
  if (bytes.size() < 2) return false;
  value = uint16_t(bytes[0] << 8 | bytes[1]);
  bytes = bytes.subspan(2);
  return true;
}
bool Get32(std::span<const uint8_t>& bytes, uint32_t& value) {
  if (bytes.size() < 4) return false;
  value = 0;
  for (int i = 0; i < 4; ++i) value = (value << 8) | bytes[i];
  bytes = bytes.subspan(4);
  return true;
}
bool Get64(std::span<const uint8_t>& bytes, uint64_t& value) {
  if (bytes.size() < 8) return false;
  value = 0;
  for (int i = 0; i < 8; ++i) value = (value << 8) | bytes[i];
  bytes = bytes.subspan(8);
  return true;
}

void Header(std::vector<uint8_t>& bytes, uint8_t kind, uint64_t nonce) {
  bytes = {'G', 'R', 'L', 'Y', kVersion, kind, 0};
  Put64(bytes, nonce);
}

bool ParseHeader(std::span<const uint8_t> bytes, uint8_t& kind, uint64_t& nonce,
                 std::span<const uint8_t>& body) {
  if (bytes.size() < 15 || std::memcmp(bytes.data(), "GRLY", 4) != 0 || bytes[4] != kVersion ||
      bytes[6] != 0)
    return false;
  kind = bytes[5];
  body = bytes.subspan(7);
  return Get64(body, nonce);
}

const char* StatusText(uint32_t status) {
  // Shared with the relay: docs/plans/giants-relay.md "Status and failure codes".
  switch (status) {
    case 0: return "";
    case 1: return "malformed lobby request";
    case 2: return "lobby protocol version mismatch";
    case 3: return "room not found";
    case 4: return "room is full";
    case 5: return "room code collision";
    case 6: return "netplay build mismatch";
    case 7: return "no relay ports are available";
    case 8: return "lobby is full";
    case 9: return "rate limited by the lobby";
    case 10: return "the room has no host hello yet";
    case 11: return "lobby message too large";
    default: return "unknown lobby error";
  }
}

std::string Trim(std::string text) {
  const auto not_space = [](unsigned char c) { return std::isspace(c) == 0; };
  text.erase(text.begin(), std::find_if(text.begin(), text.end(), not_space));
  text.erase(std::find_if(text.rbegin(), text.rend(), not_space).base(), text.end());
  return text;
}

uint64_t Random64() {
  static thread_local std::mt19937_64 rng([] {
    std::random_device random;
    std::seed_seq seed{random(), random(), random(), random()};
    return std::mt19937_64(seed);
  }());
  return rng();
}

uint32_t BuildId(const std::string& hash) {
  // The same value the relay sees: the first eight hex characters as a big-endian u32, so the
  // build check agrees with docs/plans/giants-relay.md regardless of the hash length.
  uint32_t id = 0;
  for (size_t i = 0; i < 8 && i < hash.size(); ++i) {
    const unsigned char c = static_cast<unsigned char>(hash[i]);
    const uint32_t digit = (c >= '0' && c <= '9') ? c - '0'
                           : (c >= 'a' && c <= 'f') ? c - 'a' + 10
                           : (c >= 'A' && c <= 'F') ? c - 'A' + 10
                                                    : 0;
    id = (id << 4) | digit;
  }
  return id;
}

bool SameAddress(const ENetAddress& a, const ENetAddress& b) {
  return a.host == b.host && a.port == b.port;
}

std::string FormatAddress(const ENetAddress& address) {
  char ip[64] = {};
  if (enet_address_get_host_ip(&address, ip, sizeof(ip)) != 0) return {};
  return std::string(ip) + ":" + std::to_string(address.port);
}

bool Send(ENetHost& host, const ENetAddress& to, const std::vector<uint8_t>& bytes) {
  ENetBuffer buffer{};
  buffer.data = const_cast<uint8_t*>(bytes.data());
  buffer.dataLength = bytes.size();
  const int sent = enet_socket_send(host.socket, &to, &buffer, 1);
  return sent == int(bytes.size()) || sent == 0;  // 0 = would block; retry next tick.
}

thread_local LobbyClient* t_lobby = nullptr;
int ENET_CALLBACK LobbyIntercept(ENetHost* host, ENetEvent* event) {
  return t_lobby ? t_lobby->Intercept(*host, event) : 0;
}
}  // namespace

bool ParseLobbyEndpoint(const std::string& endpoint, std::string& host, uint16_t& port) {
  const std::string text = Trim(endpoint);
  if (text.empty()) return false;
  const auto colon = text.rfind(':');
  uint32_t number = kDefaultLobbyPort;
  if (colon == std::string::npos) {
    host = text;
  } else {
    if (colon == 0 || colon + 1 == text.size()) return false;
    host = text.substr(0, colon);
    const auto tail = text.substr(colon + 1);
    auto [end, ec] = std::from_chars(tail.data(), tail.data() + tail.size(), number);
    if (ec != std::errc() || end != tail.data() + tail.size() || number == 0 || number > 65535)
      return false;
  }
  if (host.size() > 255 || host.find_first_of(" \t/") != std::string::npos) return false;
  port = uint16_t(number);
  return true;
}

bool ParseLobbyEndpoints(const std::string& endpoints,
                         std::vector<std::pair<std::string, uint16_t>>& out) {
  out.clear();
  size_t start = 0;
  while (start <= endpoints.size()) {
    const auto stop = endpoints.find_first_of(";,", start);
    const auto token = endpoints.substr(start, stop == std::string::npos ? stop : stop - start);
    if (!Trim(token).empty()) {
      std::string host;
      uint16_t port = 0;
      if (!ParseLobbyEndpoint(token, host, port)) {
        out.clear();
        return false;
      }
      const bool duplicate = std::any_of(out.begin(), out.end(), [&](const auto& entry) {
        return entry.first == host && entry.second == port;
      });
      if (!duplicate) out.emplace_back(std::move(host), port);
    }
    if (stop == std::string::npos) break;
    start = stop + 1;
  }
  return !out.empty();
}

bool ValidRoomCode(std::string_view code) {
  return code.size() == kRoomCodeLength && std::all_of(code.begin(), code.end(), [](char c) {
    return std::isxdigit(static_cast<unsigned char>(c)) != 0;
  });
}

LobbyClient::LobbyClient(bool host, std::string lobby_endpoints, std::string room_code,
                         uint16_t gameplay_port, const std::string& build_hash)
    : host_(host),
      lobby_endpoints_(std::move(lobby_endpoints)),
      room_code_(std::move(room_code)),
      gameplay_port_(gameplay_port),
      build_id_(BuildId(build_hash)) {}

LobbyClient::~LobbyClient() { active_ = false; }

bool LobbyClient::Begin(ENetHost& host, std::string& error) {
  if (!host_) {
    if (!ValidRoomCode(room_code_)) {
      error = "Room code must be 8 hexadecimal characters";
      return false;
    }
    std::transform(room_code_.begin(), room_code_.end(), room_code_.begin(),
                   [](unsigned char c) { return char(std::toupper(c)); });
  }
  if (!ParseLobbyEndpoints(lobby_endpoints_, endpoints_)) {
    error = "netplay_lobby must be host[:port], separated by ';'";
    return false;
  }
  endpoint_index_ = 0;
  if (!SelectEndpoint(0, error)) return false;
  nonce_ = Random64();
  active_ = true;
  state_ = host_ ? State::kCreating : State::kJoining;
  const auto now = Clock::now();
  // A LAN candidate that does not answer costs this much before the external address is tried.
  deadline_ = now + std::chrono::milliseconds(1500);
  next_send_ = now;
  t_lobby = this;
  host.intercept = &LobbyIntercept;
  if (!SendRequest(host, error)) {
    Shutdown(host);
    return false;
  }
  return true;
}

bool LobbyClient::SelectEndpoint(size_t index, std::string& error) {
  if (index >= endpoints_.size()) {
    error = "No lobby endpoint left";
    return false;
  }
  control_ = {};
  if (enet_address_set_host(&control_, endpoints_[index].first.c_str()) != 0) {
    error = "Cannot resolve lobby " + endpoints_[index].first;
    return false;
  }
  control_.port = endpoints_[index].second;
  return true;
}

bool LobbyClient::SendRequest(ENetHost& host, std::string& error) {
  std::vector<uint8_t> packet;
  Header(packet, host_ ? kCreate : kJoin, nonce_);
  if (host_) {
    Put16(packet, kProtocolVersion);
    Put32(packet, build_id_);
    Put16(packet, gameplay_port_);
  } else {
    packet.insert(packet.end(), room_code_.begin(), room_code_.end());
    Put32(packet, build_id_);
  }
  if (!Send(host, control_, packet)) {
    error = "Could not send the lobby request";
    return false;
  }
  next_send_ = Clock::now() + std::chrono::seconds(1);
  return true;
}

void LobbyClient::SendHello(ENetHost& host) {
  std::vector<uint8_t> packet{'G', 'R', 'E', 'L', kVersion};
  packet.insert(packet.end(), room_.begin(), room_.end());
  // Echo the CREATE nonce: the relay ties the hello to the room it allocated.
  Put64(packet, nonce_);
  Send(host, relay_, packet);
}

void LobbyClient::SendKeepalive(ENetHost& host) {
  std::vector<uint8_t> packet;
  Header(packet, kKeepalive, Random64());
  packet.insert(packet.end(), room_.begin(), room_.end());
  Send(host, control_, packet);
}

void LobbyClient::Tick(ENetHost& host, Clock::time_point now) {
  if (!active_ || failed_) return;
  switch (state_) {
    case State::kCreating:
    case State::kJoining:
      if (now >= deadline_) {
        if (endpoint_index_ + 1 < endpoints_.size()) {
          // LAN candidate did not answer: move to the external address with a fresh nonce.
          ++endpoint_index_;
          std::string error;
          if (!SelectEndpoint(endpoint_index_, error)) {
            Fail(error);
            return;
          }
          nonce_ = Random64();
          deadline_ = now + std::chrono::milliseconds(1500);
          next_send_ = now;
          if (!SendRequest(host, error)) Fail(error);
          return;
        }
        Fail("Lobby did not respond");
        return;
      }
      if (now >= next_send_) {
        std::string error;
        if (!SendRequest(host, error)) Fail(error);
      }
      break;
    case State::kHello:
      // The host waits for a client here: no local deadline; the session timeout bounds it.
      if (now >= next_hello_) {
        SendHello(host);
        next_hello_ = now + std::chrono::seconds(1);
      }
      if (now >= next_keepalive_) {
        SendKeepalive(host);
        next_keepalive_ = now + std::chrono::seconds(10);
      }
      break;
    case State::kReady:
      if (host_) {
        // Keep both the control room and the relay NAT mapping alive: ENet can be idle in menus,
        // and a symmetric NAT would otherwise drop the 411xx pinhole.
        if (now >= next_hello_) {
          SendHello(host);
          next_hello_ = now + std::chrono::seconds(5);
        }
        if (now >= next_keepalive_) {
          SendKeepalive(host);
          next_keepalive_ = now + std::chrono::seconds(10);
        }
      }
      break;
    default:
      break;
  }
}

int LobbyClient::Intercept(ENetHost& host, ENetEvent*) {
  if (!active_) return 0;
  if (SameAddress(host.receivedAddress, control_)) {
    HandleControl(host);
    return 1;
  }
  if (relay_.port != 0 && SameAddress(host.receivedAddress, relay_)) {
    if (host.receivedDataLength >= 4 &&
        std::memcmp(host.receivedData, "GRAK", 4) == 0) {
      return 1;
    }
    return 0;  // ENet traffic between host and client must reach ENet.
  }
  return 0;
}

void LobbyClient::HandleControl(ENetHost& host) {
  std::span<const uint8_t> bytes{host.receivedData, host.receivedDataLength};
  uint8_t kind = 0;
  uint64_t nonce = 0;
  std::span<const uint8_t> body;
  if (!ParseHeader(bytes, kind, nonce, body)) return;
  if (kind == kError) {
    uint32_t status = 0;
    if (nonce == nonce_ && Get32(body, status) && body.empty()) Fail(StatusText(status));
    return;
  }
  if (kind == kKeepaliveAck) {
    uint32_t status = 0;
    // The relay lost the room (restart or expiry): surface it instead of retrying forever.
    if (Get32(body, status) && status == 3) Fail("the relay lost the room");
    return;
  }
  if (nonce != nonce_) return;  // stale or spoofed reply
  if (kind == kCreateAck && host_ && state_ == State::kCreating) {
    uint32_t status = 0;
    uint16_t relay_port = 0;
    if (!Get32(body, status)) return;
    if (status != 0) {
      Fail(StatusText(status));
      return;
    }
    if (!Get16(body, relay_port) || body.size() < kRoomCodeLength) return;
    room_.assign(reinterpret_cast<const char*>(body.data()), kRoomCodeLength);
    body = body.subspan(kRoomCodeLength);
    uint16_t ttl = 0;
    if (!Get16(body, ttl) || !body.empty() || !ValidRoomCode(room_)) return;
    (void)ttl;  // Advertised lifetime; the client refreshes by keepalive instead of counting down.
    relay_ = control_;
    relay_.port = relay_port;
    state_ = State::kHello;
    next_hello_ = Clock::now();
    next_keepalive_ = Clock::now() + std::chrono::seconds(10);
    SendHello(host);
    return;
  }
  if (kind == kJoinAck && !host_ && state_ == State::kJoining) {
    uint32_t status = 0;
    if (!Get32(body, status)) return;
    if (status != 0) {
      Fail(StatusText(status));
      return;
    }
    if (body.size() < 8) return;
    uint32_t ip = 0;
    std::memcpy(&ip, body.data(), 4);
    body = body.subspan(4);
    uint16_t relay_port = 0;
    uint16_t gameplay_port = 0;
    if (!Get16(body, relay_port) || !Get16(body, gameplay_port) || !body.empty()) return;
    (void)gameplay_port;  // Informational: the relay, not the host port, is the connect target.
    // Use the control endpoint we actually reached, not the advertised address: a LAN client that
    // used the LAN lobby address must not be sent to the public address (NAT hairpin fails), while a
    // WAN client already reached the public one. The advertised IP is still parsed and validated.
    (void)ip;
    relay_.host = control_.host;
    relay_.port = relay_port;
    state_ = State::kConnecting;
    connect_ready_ = true;
  }
}

void LobbyClient::OnConnected() {
  if (!active_) return;
  state_ = State::kReady;
}

void LobbyClient::Shutdown(ENetHost& host) {
  active_ = false;
  if (host.intercept == &LobbyIntercept) host.intercept = nullptr;
  if (t_lobby == this) t_lobby = nullptr;
}

void LobbyClient::Fail(const std::string& message) {
  if (failed_) return;
  failed_ = true;
  error_ = message;
  state_ = State::kFailed;
}

std::string LobbyClient::relay_endpoint() const {
  if (relay_.port == 0) return {};
  return FormatAddress(relay_);
}

std::string LobbyClient::state() const {
  switch (state_) {
    case State::kIdle: return "direct";
    case State::kCreating: return "creating room";
    case State::kJoining: return "joining room";
    case State::kHello: return host_ ? "waiting for client" : "waiting for relay";
    case State::kConnecting: return "connecting to relay";
    case State::kReady: return "connected";
    case State::kFailed: return "lobby error";
  }
  return "unknown";
}

}  // namespace giantsrecomp::netplay
