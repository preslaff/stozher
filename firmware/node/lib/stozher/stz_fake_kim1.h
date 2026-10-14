// A scripted KIM1 and an in-memory sequence store, for host tests and the hardware-in-the-loop demo build.
// The fake behaves like the module as far as the product sheet says: after a correct AT+TX it raises TX_STATUS
// for a while and then answers +TX_INFO: 1 (or 0 when told to fail, or says nothing when told to stay silent).
#pragma once
#include <string.h>

#include "stz_kim1.h"
#include "stz_tx.h"

namespace stz {

class FakeKim1 : public kim1::Port {
 public:
  enum class Reply { Done, Fail, Silent };

  size_t write(const uint8_t* data, size_t len) override {
    if (len > sizeof cmd_ - 1) len = sizeof cmd_ - 1;
    memcpy(cmd_, data, len);
    cmd_[len] = 0;
    commands++;
    pending_ = true;
    busy_ = true;
    done_at_ = clock_ + air_ms;
    return len;
  }
  int read() override {
    if (rx_pos_ < rx_len_) return rx_[rx_pos_++];
    return -1;
  }
  bool tx_status() override { return busy_ || force_busy_pin; }

  // Move the module's own clock to `now` (call it with the same time the driver sees).
  void step(uint32_t now) {
    clock_ = now;
    if (pending_ && (int32_t)(now - done_at_) >= 0) {
      pending_ = false;
      busy_ = false;
      if (reply == Reply::Done) say("+TX_INFO: 1\r\n");
      else if (reply == Reply::Fail) say("+TX_INFO: 0\r\n");
    }
  }
  void say(const char* text) {
    rx_len_ = strlen(text) < sizeof rx_ ? strlen(text) : sizeof rx_;
    memcpy(rx_, text, rx_len_);
    rx_pos_ = 0;
  }
  const char* last_command() const { return cmd_; }

  Reply reply = Reply::Done;
  uint32_t air_ms = 1500;
  int commands = 0;
  bool force_busy_pin = false;  // lets a test raise TX_STATUS without any command

 private:
  char cmd_[160] = {0};
  uint8_t rx_[64];
  size_t rx_len_ = 0, rx_pos_ = 0;
  bool pending_ = false, busy_ = false;
  uint32_t done_at_ = 0, clock_ = 0;
};

class FakeStore : public SeqStore {
 public:
  Result load(uint8_t key_id, uint16_t& next) override {
    if (fail_load) return Result::Error;
    if (!has_[key_id & 15]) return Result::Missing;
    next = value_[key_id & 15];
    return Result::Ok;
  }
  bool save(uint8_t key_id, uint16_t next) override {
    if (fail_save) return false;
    saves++;
    if (lie_about_save) return true;  // claims success without storing
    has_[key_id & 15] = true;
    value_[key_id & 15] = next;
    return true;
  }
  bool fail_load = false, fail_save = false, lie_about_save = false;
  int saves = 0;

 private:
  bool has_[16] = {false};
  uint16_t value_[16] = {0};
};

}  // namespace stz
