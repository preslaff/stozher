// A scripted Meshtastic Serial-module port for host tests.
#pragma once
#include <string.h>

#include "stz_mesh.h"

namespace stz {

class FakeMesh : public mesh::Port {
 public:
  size_t write(const uint8_t* d, size_t n) override {
    if (fail_writes) return 0;
    if (out_len + n < sizeof out) { memcpy(out + out_len, d, n); out_len += n; }
    writes++;
    return n;
  }
  int read() override { return rx_pos < rx_len ? rx[rx_pos++] : -1; }
  void say(const char* text) {  // appended, as lines arriving from the T-Beam
    size_t n = strlen(text);
    if (rx_len + n < sizeof rx) { memcpy(rx + rx_len, text, n); rx_len += n; }
  }
  // The last full line written, without its line end.
  const char* last_line() {
    out[out_len] = 0;
    size_t end = out_len;
    while (end > 0 && (out[end - 1] == '\n' || out[end - 1] == '\r')) end--;
    size_t start = end;
    while (start > 0 && out[start - 1] != '\n') start--;
    memcpy(last, out + start, end - start);
    last[end - start] = 0;
    return last;
  }
  int writes = 0;
  bool fail_writes = false;
  char out[2048];
  size_t out_len = 0;

 private:
  char rx[1024];
  size_t rx_len = 0, rx_pos = 0;
  char last[256];
};

}  // namespace stz
