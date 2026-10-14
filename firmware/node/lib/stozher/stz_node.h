// The node's decision loop, free of hardware: epochs in, detection, records queued for the uplink.
// Boards only supply the byte streams (UBX in, KIM1) and call on_epoch()/tick().
#pragma once
#include <stdint.h>

#include "stz_detect.h"
#include "stz_gnss.h"
#include "stz_tx.h"

namespace stz {

class Node {
 public:
  Node(const DetectConfig& cfg, Tx& tx) : detector_(cfg), tx_(tx) {}

  // One finished GNSS epoch. Marks it blanked while our own transmitters are active, runs the rules, and queues
  // an event when a class starts (or changes) and a status change when it ends. Returns the verdict.
  Verdict on_epoch(Epoch e, uint32_t now_ms);

  // Call every loop: queues the hourly heartbeat (once there is a position to report) and drives the uplink.
  void tick(uint32_t now_ms);

  uint8_t current_class() const { return class_; }
  SubmitResult last_submit() const { return last_submit_; }

 private:
  Detector detector_;
  Tx& tx_;
  Epoch last_epoch_;
  bool have_epoch_ = false;
  uint8_t class_ = kNormal;
  SubmitResult last_submit_ = SubmitResult::Queued;
};

}  // namespace stz
