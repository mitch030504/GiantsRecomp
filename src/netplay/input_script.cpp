#include "netplay/input_script.h"

#include <algorithm>
#include <charconv>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <sstream>

namespace giantsrecomp::netplay {

namespace {

constexpr struct {
  std::string_view name;
  uint16_t bit;
} kButtons[] = {
    {"UP", 0x0001},    {"DOWN", 0x0002}, {"LEFT", 0x0004}, {"RIGHT", 0x0008},
    {"START", 0x0010}, {"BACK", 0x0020}, {"LS", 0x0040},   {"RS", 0x0080},
    {"LB", 0x0100},    {"RB", 0x0200},   {"A", 0x1000},    {"B", 0x2000},
    {"X", 0x4000},     {"Y", 0x8000},
};

enum AnalogBit : uint8_t { kLT = 1, kRT = 2, kLX = 4, kLY = 8, kRX = 16, kRY = 32 };

bool ParseNumber(std::string_view text, double& out) {
  // std::from_chars for double isn't everywhere yet; strtod on a copy is.
  std::string copy(text);
  char* end = nullptr;
  out = std::strtod(copy.c_str(), &end);
  return end && *end == '\0' && !copy.empty();
}

int16_t Stick(double v) { return int16_t(std::lround(std::clamp(v, -1.0, 1.0) * 32767.0)); }
uint8_t Trigger(double v) { return uint8_t(std::lround(std::clamp(v, 0.0, 1.0) * 255.0)); }

}  // namespace

bool InputScript::Parse(std::string_view text, std::string* error) {
  lines_.clear();
  end_frame_ = 0;
  std::istringstream stream{std::string(text)};
  std::string raw;
  int line_number = 0;
  while (std::getline(stream, raw)) {
    ++line_number;
    if (auto hash = raw.find('#'); hash != std::string::npos) {
      raw.resize(hash);
    }
    std::istringstream words(raw);
    std::string first_word, count_word;
    if (!(words >> first_word)) {
      continue;  // blank or comment
    }
    auto fail = [&](const std::string& what) {
      if (error) {
        *error = "line " + std::to_string(line_number) + ": " + what;
      }
      return false;
    };
    Line line;
    if (!(words >> count_word) ||
        std::from_chars(first_word.data(), first_word.data() + first_word.size(), line.first).ec !=
            std::errc() ||
        std::from_chars(count_word.data(), count_word.data() + count_word.size(), line.count).ec !=
            std::errc()) {
      return fail("expected <first frame> <frame count>");
    }
    std::string token;
    while (words >> token) {
      std::transform(token.begin(), token.end(), token.begin(),
                     [](unsigned char c) { return char(std::toupper(c)); });
      if (auto eq = token.find('='); eq != std::string::npos) {
        const std::string name = token.substr(0, eq);
        double value;
        if (!ParseNumber(std::string_view(token).substr(eq + 1), value)) {
          return fail("bad value in " + token);
        }
        if (name == "LT") {
          line.pad.left_trigger = Trigger(value), line.analog_mask |= kLT;
        } else if (name == "RT") {
          line.pad.right_trigger = Trigger(value), line.analog_mask |= kRT;
        } else if (name == "LX") {
          line.pad.thumb_lx = Stick(value), line.analog_mask |= kLX;
        } else if (name == "LY") {
          line.pad.thumb_ly = Stick(value), line.analog_mask |= kLY;
        } else if (name == "RX") {
          line.pad.thumb_rx = Stick(value), line.analog_mask |= kRX;
        } else if (name == "RY") {
          line.pad.thumb_ry = Stick(value), line.analog_mask |= kRY;
        } else {
          return fail("unknown input " + name);
        }
        continue;
      }
      auto button = std::find_if(std::begin(kButtons), std::end(kButtons),
                                 [&](const auto& b) { return b.name == token; });
      if (button == std::end(kButtons)) {
        return fail("unknown input " + token);
      }
      line.pad.buttons |= button->bit;
    }
    end_frame_ = std::max(end_frame_, line.first + line.count);
    lines_.push_back(line);
  }
  return true;
}

bool InputScript::Load(const std::filesystem::path& path, std::string* error) {
  std::ifstream file(path, std::ios::binary);
  if (!file) {
    if (error) {
      *error = "can't open the file";
    }
    return false;
  }
  std::ostringstream text;
  text << file.rdbuf();
  return Parse(text.str(), error);
}

ScriptPad InputScript::PadAt(uint64_t frame) const {
  ScriptPad pad;
  for (const Line& line : lines_) {
    if (frame < line.first || frame >= line.first + line.count) {
      continue;
    }
    pad.buttons |= line.pad.buttons;
    if (line.analog_mask & kLT) pad.left_trigger = line.pad.left_trigger;
    if (line.analog_mask & kRT) pad.right_trigger = line.pad.right_trigger;
    if (line.analog_mask & kLX) pad.thumb_lx = line.pad.thumb_lx;
    if (line.analog_mask & kLY) pad.thumb_ly = line.pad.thumb_ly;
    if (line.analog_mask & kRX) pad.thumb_rx = line.pad.thumb_rx;
    if (line.analog_mask & kRY) pad.thumb_ry = line.pad.thumb_ry;
  }
  return pad;
}

}  // namespace giantsrecomp::netplay
