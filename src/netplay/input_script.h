// Scripted controller input for player 1 (input_script), so a gameplay recording for the netplay
// and upscaler checks can be made without anyone at the controller. Each line holds inputs for a
// range of guest frames (VdSwap count; stable with clock_frame_locked):
//
//   # comment
//   <first frame> <frame count> <input>...
//
// Inputs: A B X Y START BACK LB RB LS RS UP DOWN LEFT RIGHT (held), LT=<0..1> RT=<0..1>,
// LX=<-1..1> LY=<-1..1> RX=<-1..1> RY=<-1..1> (stick up is +LY). Overlapping lines combine.
// Combined with input_record, the run becomes an ordinary recording for input_replay.

#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

namespace giantsrecomp::netplay {

// Host-order pad state; X_INPUT_GAMEPAD button bits.
struct ScriptPad {
  uint16_t buttons = 0;
  uint8_t left_trigger = 0;
  uint8_t right_trigger = 0;
  int16_t thumb_lx = 0;
  int16_t thumb_ly = 0;
  int16_t thumb_rx = 0;
  int16_t thumb_ry = 0;
};

class InputScript {
 public:
  // Fails with the line number on a malformed line or an unknown input.
  bool Parse(std::string_view text, std::string* error);
  bool Load(const std::filesystem::path& path, std::string* error);

  // The pad for a guest frame: every line covering it combined; neutral outside all of them.
  ScriptPad PadAt(uint64_t frame) const;
  // The frame after the last line ends.
  uint64_t end_frame() const { return end_frame_; }

 private:
  struct Line {
    uint64_t first = 0;
    uint64_t count = 0;
    ScriptPad pad;
    // Which analog fields this line sets, so a later line's zero doesn't override an earlier one.
    uint8_t analog_mask = 0;
  };
  std::vector<Line> lines_;
  uint64_t end_frame_ = 0;
};

}  // namespace giantsrecomp::netplay
