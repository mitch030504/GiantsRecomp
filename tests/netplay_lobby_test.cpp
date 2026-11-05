#include "netplay/lobby.h"

#include <cstdio>

using namespace giantsrecomp::netplay;

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

int main() {
  std::string host;
  uint16_t port = 0;

  CHECK(ParseLobbyEndpoint("192.168.8.2:32200", host, port));
  CHECK(host == "192.168.8.2" && port == 32200);
  CHECK(ParseLobbyEndpoint("matchmaking.example.com:41000", host, port));
  CHECK(host == "matchmaking.example.com" && port == 41000);
  CHECK(ParseLobbyEndpoint("192.168.8.2", host, port));
  CHECK(host == "192.168.8.2" && port == kDefaultLobbyPort);
  CHECK(!ParseLobbyEndpoint(":32200", host, port));
  CHECK(!ParseLobbyEndpoint("host:0", host, port));
  CHECK(!ParseLobbyEndpoint("host:65536", host, port));
  CHECK(!ParseLobbyEndpoint("host:abc", host, port));
  CHECK(!ParseLobbyEndpoint("host:", host, port));
  CHECK(!ParseLobbyEndpoint("bad host:32200", host, port));

  std::vector<std::pair<std::string, uint16_t>> endpoints;
  CHECK(ParseLobbyEndpoints("192.168.8.2:32200;188.91.1.230:32200", endpoints));
  CHECK(endpoints.size() == 2);
  CHECK(endpoints[0].first == "192.168.8.2" && endpoints[0].second == 32200);
  CHECK(endpoints[1].first == "188.91.1.230" && endpoints[1].second == 32200);
  CHECK(ParseLobbyEndpoints("relay.local", endpoints));
  CHECK(endpoints.size() == 1 && endpoints[0].first == "relay.local");
  CHECK(endpoints[0].second == kDefaultLobbyPort);
  CHECK(!ParseLobbyEndpoints("", endpoints));
  CHECK(!ParseLobbyEndpoints("host:0;other:1", endpoints));

  CHECK(ValidRoomCode("A1B2C3D4"));
  CHECK(ValidRoomCode("a1b2c3d4"));
  CHECK(!ValidRoomCode("A1B2C3D"));
  CHECK(!ValidRoomCode("A1B2C3D4E"));
  CHECK(!ValidRoomCode("G1B2C3D4"));
  CHECK(!ValidRoomCode(""));

  return failures ? 1 : 0;
}
