// The node's decision loop, free of hardware: epochs in, detection, records queued for the uplink.
// Boards only supply the byte streams (UBX in, KIM1) and call on_epoch()/tick().
#pragma once
#include <stdint.h>

#include "stz_detect.h"
#include "stz_gnss.h"
#include "stz_tx.h"

namespace stz {

namespace mesh {
class Link;
}

class Node {
 public:
  // `mesh` is optional: without it the node is exactly the satellite-only node. With it, every queued record is
  // also sent as mesh text, and verified acknowledgements arriving by mesh reach the uplink.
  // Define STZ_NO_MESH to leave the mesh code out of the image altogether (the Leonardo build does).
  Node(const DetectConfig& cfg, Tx& tx, mesh::Link* mesh = nullptr);

  // One finished GNSS epoch. Marks it blanked while our own transmitters are active, runs the rules, and queues
  // an event when a class starts (or changes) and a status change when it ends. Returns the verdict.
  Verdict on_epoch(Epoch e, uint32_t now_ms);

  // Call every loop: queues the hourly heartbeat (once there is a position to report) and drives the uplink.
  void tick(uint32_t now_ms);

  bool mesh_present() const {
#ifdef STZ_NO_MESH
    return false;
#else
    return mesh_ != nullptr;
#endif
  }
  // Records built from synthetic epochs must never pass as live ones: in test mode every record is type `test`.
  void set_test_mode(bool on) { test_mode_ = on; }
  Tx& tx() { return tx_; }
  uint8_t current_class() const { return class_; }
  const Detector& detector() const { return detector_; }
  SubmitResult last_submit() const { return last_submit_; }

 private:
  Detector detector_;
  Tx& tx_;
  mesh::Link* mesh_;
  Epoch last_epoch_;
  bool have_epoch_ = false;
  bool test_mode_ = false;
  uint8_t class_ = kNormal;
  uint8_t last_conf_ = 0, last_drop_ = 0;  // of the latest examined epoch: a heartbeat during an event repeats them
  SubmitResult last_submit_ = SubmitResult::Queued;
};

}  // namespace stz
