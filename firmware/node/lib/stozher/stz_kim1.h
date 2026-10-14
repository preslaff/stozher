// Kinéis KIM1 module: the AT command and its reply, and the hardware seam the driver talks through.
// Source: KIM1 product sheet (rev 1.12). `+TX_INFO: 1|0` means the module finished (or failed) its LOCAL
// transmission; it says nothing about a satellite having received the message.
// Not yet confirmed on a module: the line terminator (CR LF assumed) and the exact reply format.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace stz {
namespace kim1 {

// The hardware seam. Real boards implement it with a UART and the TX_STATUS pin; tests use a fake modem.
// Nothing here may block: `read` returns -1 when no byte is waiting.
class Port {
 public:
  virtual ~Port() {}
  virtual size_t write(const uint8_t* data, size_t len) = 0;
  virtual int read() = 0;
  virtual bool tx_status() = 0;  // KIM1 pin 11: transmission in progress
};

// A port for a node with no KIM1 attached (see TxConfig::satellite_enabled): writes nothing, reads nothing.
class NullPort : public Port {
 public:
  size_t write(const uint8_t*, size_t) override { return 0; }
  int read() override { return -1; }
  bool tx_status() override { return false; }
};

struct Params {
  char band[3] = {'B', '1', 0};  // B1..B9; the organisers give the real values (placeholder)
  uint16_t freq = 300;           // 0..600 (0..700 for B4 and B5), steps within the band
  uint16_t power_mw = 500;       // 250, 500, 750, 1000 or 1500
};

enum class CmdStatus : uint8_t { Ok, BadBand, BadFreq, BadPower, BadData, BufferTooSmall };
const char* reason(CmdStatus s);

constexpr size_t kMaxDataBytes = 32;  // 64 hex characters with a 7-digit Kinéis ID
constexpr size_t kMaxCommand = 8 + 3 + 4 + 5 + 2 * kMaxDataBytes + 3;

// "AT+TX=<band>,<freq>,<power>,<HEX>\r\n". `n` receives the length (no NUL needed by the module).
CmdStatus build_tx(const Params& p, const uint8_t* data, size_t len, char* out, size_t cap, size_t& n);

enum class Line : uint8_t { None, TxDone, TxFail, Other };

// Collects reply bytes into lines; reports `+TX_INFO: 1` / `+TX_INFO: 0`. Long garbage lines are dropped
// without overflowing.
class LineParser {
 public:
  Line feed(uint8_t byte);

 private:
  char buf_[24];
  uint8_t len_ = 0;
  bool overflow_ = false;
};

}  // namespace kim1
}  // namespace stz
