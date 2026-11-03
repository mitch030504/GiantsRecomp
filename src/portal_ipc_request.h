// Request framing for the portal IPC server: one bounded line per TCP connection.
//
// Why this is not inside portal_ipc.cpp: "only a line the client terminated may run" is the rule
// that stops a timeout, an early close or an oversized line from executing a truncated command.
// Keeping it free of sockets, clocks and the SDK lets tests/portal_ipc_request_test.cpp prove that
// rule without a live server; portal_ipc.cpp owns the connection, the deadline and the reply.

#pragma once

#include <cstddef>
#include <string>

namespace giantsrecomp::portal_ipc {

// The documented command limit (docs/portal-protocol.md); the bundled client refuses longer
// commands too, so anything past this is not a request this protocol accepted.
inline constexpr std::size_t kMaxRequestLine = 4096;

class RequestLine {
 public:
  enum class Status {
    kWaiting,    // no terminator yet; the client may still be sending
    kComplete,   // the client ended the line, so Text() is a whole command
    kOversized,  // kMaxRequestLine bytes without a terminator: what we have is truncated
  };

  // Feeds one received byte. '\r' terminates like '\n', as the original reader did; the '\n' of a
  // CRLF is left unread because the connection closes after one request.
  Status Feed(char byte) {
    if (status_ == Status::kWaiting) {
      if (byte == '\n' || byte == '\r') {
        status_ = Status::kComplete;
      } else if (text_.size() >= kMaxRequestLine) {
        status_ = Status::kOversized;
      } else {
        text_.push_back(byte);
      }
    }
    return status_;
  }

  // Whatever arrived, valid as a command only once the status is kComplete.
  const std::string& Text() const { return text_; }
  Status status() const { return status_; }

 private:
  Status status_ = Status::kWaiting;
  std::string text_;
};

// Only a terminated line is executable. Every other status means the command is partial: running
// "cre" instead of "create ..." or a cut-off path is how a timed-out request used to do damage.
inline bool Executable(RequestLine::Status status) {
  return status == RequestLine::Status::kComplete;
}

}  // namespace giantsrecomp::portal_ipc
