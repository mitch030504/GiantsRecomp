// Framing rules for the portal IPC server: which bytes become a command, and which never run.
//
// The server used to execute whatever the read loop had accumulated when its 2 s receive timeout
// fired, so a truncated line was a real command. These cases pin the replacement rule: a command
// runs only after the client terminates the line.

#include "portal_ipc_request.h"

#include <cstdio>
#include <string>

using giantsrecomp::portal_ipc::Executable;
using giantsrecomp::portal_ipc::kMaxRequestLine;
using giantsrecomp::portal_ipc::RequestLine;

static int g_failures = 0;
#define CHECK(cond)                                                        \
  do {                                                                     \
    if (!(cond)) {                                                         \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++g_failures;                                                        \
    }                                                                      \
  } while (0)

// What ReadRequest() does with a whole buffer, stopped at the first non-waiting status.
static RequestLine::Status FeedAll(const std::string& bytes, RequestLine& line) {
  RequestLine::Status status = RequestLine::Status::kWaiting;
  for (char byte : bytes) {
    status = line.Feed(byte);
    if (status != RequestLine::Status::kWaiting) break;
  }
  return status;
}

int main() {
  // A terminated line is a command and runs.
  {
    RequestLine line;
    CHECK(FeedAll("hello\n", line) == RequestLine::Status::kComplete);
    CHECK(Executable(line.status()));
    CHECK(line.Text() == "hello");
  }
  // CR terminates like LF; the LF of a CRLF arrives after the line is already complete.
  {
    RequestLine line;
    CHECK(FeedAll("clear\r", line) == RequestLine::Status::kComplete);
    CHECK(Executable(line.status()));
    CHECK(line.Text() == "clear");
  }
  {
    RequestLine line;
    CHECK(FeedAll("clear\r\n", line) == RequestLine::Status::kComplete);
    CHECK(line.Text() == "clear");
  }
  // An empty line is still a terminated request: it reaches ProcessCommand(), which answers
  // "error unknown command". Framing must not invent a rejection here.
  {
    RequestLine line;
    CHECK(FeedAll("\n", line) == RequestLine::Status::kComplete);
    CHECK(Executable(line.status()));
    CHECK(line.Text().empty());
  }
  // The regression: valid command text without a terminator is incomplete, so it never runs.
  {
    RequestLine line;
    CHECK(FeedAll("remove 3", line) == RequestLine::Status::kWaiting);
    CHECK(!Executable(line.status()));
    CHECK(line.Text() == "remove 3");
  }
  // The same for a partial word, which used to be executed as a shorter command.
  {
    RequestLine line;
    CHECK(FeedAll("hush of", line) == RequestLine::Status::kWaiting);
    CHECK(!Executable(line.status()));
  }
  // Exactly the limit plus a terminator is one line.
  {
    RequestLine line;
    CHECK(FeedAll(std::string(kMaxRequestLine, 'a'), line) == RequestLine::Status::kWaiting);
    CHECK(line.Feed('\n') == RequestLine::Status::kComplete);
    CHECK(Executable(line.status()));
    CHECK(line.Text().size() == kMaxRequestLine);
  }
  // One byte past the limit is truncated, not a command: the path in a cut-off `load`/`create`
  // must not be half-applied.
  {
    RequestLine line;
    CHECK(FeedAll(std::string(kMaxRequestLine, 'a'), line) == RequestLine::Status::kWaiting);
    CHECK(line.Feed('b') == RequestLine::Status::kOversized);
    CHECK(!Executable(line.status()));
    CHECK(line.Text().size() == kMaxRequestLine);
  }
  // Once a line is complete, later bytes cannot extend or restart it.
  {
    RequestLine line;
    CHECK(FeedAll("hello\n", line) == RequestLine::Status::kComplete);
    CHECK(line.Feed('x') == RequestLine::Status::kComplete);
    CHECK(line.Feed('\n') == RequestLine::Status::kComplete);
    CHECK(line.Text() == "hello");
  }
  if (g_failures) std::fprintf(stderr, "%d portal IPC framing check(s) failed\n", g_failures);
  return g_failures ? 1 : 0;
}
