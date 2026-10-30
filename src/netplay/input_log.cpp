#include "netplay/input_log.h"

#include <cstdio>
#include <cstring>

namespace giantsrecomp::netplay {

namespace {

constexpr char kMagic[8] = {'G', 'R', 'I', 'N', 'P', 'U', 'T', '1'};

// On disk: kind, user, 2 zero bytes, index and result as little-endian u32, then 16 data bytes.
constexpr size_t kRecordSize = 28;

void PutU32(uint8_t* out, uint32_t v) {
  for (int i = 0; i < 4; i++) out[i] = uint8_t(v >> (8 * i));
}

uint32_t GetU32(const uint8_t* in) {
  return uint32_t(in[0]) | uint32_t(in[1]) << 8 | uint32_t(in[2]) << 16 | uint32_t(in[3]) << 24;
}

}  // namespace

InputRecorder::~InputRecorder() {
  if (file_) std::fclose(file_);
}

bool InputRecorder::Open(const std::filesystem::path& path) {
#ifdef _WIN32
  file_ = _wfopen(path.c_str(), L"wb");
#else
  file_ = std::fopen(path.c_str(), "wb");
#endif
  return file_ && std::fwrite(kMagic, sizeof(kMagic), 1, file_) == 1;
}

void InputRecorder::Write(const InputRead& read) {
  if (!file_) return;
  uint8_t record[kRecordSize] = {};
  record[0] = uint8_t(read.kind);
  record[1] = read.user;
  PutU32(record + 4, read.index);
  PutU32(record + 8, read.result);
  std::memcpy(record + 12, read.data.data(), read.data.size());
  std::fwrite(record, sizeof(record), 1, file_);
  std::fflush(file_);
}

bool InputReplay::Load(const std::filesystem::path& path, std::string* error) {
#ifdef _WIN32
  std::FILE* file = _wfopen(path.c_str(), L"rb");
#else
  std::FILE* file = std::fopen(path.c_str(), "rb");
#endif
  if (!file) {
    *error = "can't open the file";
    return false;
  }
  char magic[sizeof(kMagic)] = {};
  if (std::fread(magic, sizeof(magic), 1, file) != 1 ||
      std::memcmp(magic, kMagic, sizeof(kMagic)) != 0) {
    std::fclose(file);
    *error = "not an input recording";
    return false;
  }
  for (auto& by_user : reads_) {
    for (auto& reads : by_user) reads.clear();
  }
  uint8_t record[kRecordSize];
  while (std::fread(record, 1, sizeof(record), file) == sizeof(record)) {
    InputRead read;
    if (record[0] > uint8_t(ReadKind::kKeystroke) || record[1] >= kMaxUsers) {
      std::fclose(file);
      *error = "corrupt record";
      return false;
    }
    read.kind = ReadKind(record[0]);
    read.user = record[1];
    read.index = GetU32(record + 4);
    read.result = GetU32(record + 8);
    std::memcpy(read.data.data(), record + 12, read.data.size());
    auto& reads = reads_[record[0]][read.user];
    // Written in read order, so each list's position is its index; anything else is a bad file.
    if (read.index != reads.size()) {
      std::fclose(file);
      *error = "records out of order";
      return false;
    }
    reads.push_back(read);
  }
  // A partial last record (the game crashed mid-write) is dropped; everything before it is good.
  std::fclose(file);
  return true;
}

std::optional<InputRead> InputReplay::Find(ReadKind kind, uint8_t user, uint32_t index) const {
  if (user >= kMaxUsers) return std::nullopt;
  const auto& reads = reads_[size_t(kind)][user];
  if (index >= reads.size()) return std::nullopt;
  return reads[index];
}

size_t InputReplay::size() const {
  size_t n = 0;
  for (const auto& by_user : reads_) {
    for (const auto& reads : by_user) n += reads.size();
  }
  return n;
}

}  // namespace giantsrecomp::netplay
