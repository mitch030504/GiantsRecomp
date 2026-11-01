#include "netplay/lockstep.h"
#include <chrono>
#include <cstdio>
#include <future>
#include <thread>
using namespace giantsrecomp::netplay;
using namespace std::chrono_literals;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

int main() {
  InputFrame frame, decoded;
  frame.state[3] = 17;
  frame.hash = UINT64_MAX;
  frame.portal.push_back({3, std::array<uint8_t, 1024>{}});
  frame.portal.push_back({8, std::nullopt});
  auto bytes = EncodeInput(frame);
  CHECK(DecodeInput(bytes, decoded) && decoded == frame);
  for (size_t n = 0; n < bytes.size(); ++n) CHECK(!DecodeInput(std::span(bytes).first(n), decoded));
  bytes.push_back(0); CHECK(!DecodeInput(bytes, decoded));
  frame.portal[1].slot = 3;
  CHECK(EncodeInput(frame).empty());
  frame.portal[0].slot = 16;
  CHECK(EncodeInput(frame).empty());

  for (uint32_t delay : {0u, 3u, 12u}) {
    Session h, c;
    SessionOptions ho;
    ho.host = true; ho.port = 0; ho.delay = delay; ho.timeout = 2s;
    const SessionIdentity identity{std::string(64, 'a'), std::string(64, 'b'), ""};
    std::string error;
    CHECK(h.Start(ho, identity, {}, error));
    for (int n = 0; !h.status().port && n < 500; ++n) std::this_thread::sleep_for(2ms);
    SessionOptions co;
    co.port = h.status().port; co.timeout = 2s;
    CHECK(c.Start(co, identity, {}, error));
    CHECK(h.WaitReady(error)); CHECK(c.WaitReady(error));
    auto run = [delay](Session& session, bool host) {
      Lockstep sync(session, host);
      std::string error;
      for (uint32_t index = 0; index < 200; ++index) {
        InputFrame local;
        local.state[0] = host ? 1 : 2;
        local.state[1] = uint8_t(index);
        if (index % 20 == 0) local.hash = index;
        if (index == 10) local.portal.push_back({uint8_t(host ? 0 : 1), std::array<uint8_t, 1024>{}});
        std::array<InputFrame, 2> pair;
        if (!sync.Advance(local, pair, error)) { std::fprintf(stderr, "%s\n", error.c_str()); return false; }
        if (index < delay) { if (pair != std::array<InputFrame, 2>{}) return false; }
        else {
          if (pair[0].state[0] != 1 || pair[1].state[0] != 2 ||
              pair[0].state[1] != uint8_t(index - delay) || pair[0].state[1] != pair[1].state[1]) return false;
          if (index - delay == 10 && (pair[0].portal.size() != 1 || pair[1].portal.size() != 1)) return false;
        }
      }
      return true;
    };
    auto remote = std::async(std::launch::async, [&] { return run(c, false); });
    CHECK(run(h, true)); CHECK(remote.get());
  }
  return failures ? 1 : 0;
}
