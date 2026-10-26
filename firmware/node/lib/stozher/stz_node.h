// The node's decision loop, free of hardware: epochs in, detection, records queued for the uplink.
// Boards only supply the byte streams (UBX in, KIM1) and call on_epoch()/tick().
#pragma once
#include <stdint.h>

#include "stz_detect.h"
#include "stz_recover.h"
#include "stz_gnss.h"
#include "stz_tx.h"
#ifndef STZ_NO_MESH
#include "stz_mesh.h"
#endif

namespace stz {

namespace mesh {
class Link;
}

class Node {
 public:
  // `mesh` is optional: without it the node is exactly the satellite-only node. With it, every queued record is
  // also sent as mesh text, and verified acknowledgements arriving by mesh reach the uplink.
  // Define STZ_NO_MESH to leave the mesh code out of the image altogether (the Leonardo build does).
  // `mesh2` is a second radio on another LoRa band (docs/firmware.md, dual-band node): every record goes out on both,
  // and an ack from either frees it everywhere.
  Node(const DetectConfig& cfg, Tx& tx, mesh::Link* mesh = nullptr, mesh::Link* mesh2 = nullptr);

  // One finished GNSS epoch. Marks it blanked while our own transmitters are active, runs the rules, and queues
  // an event when a class starts (or changes) and a status change when it ends. Returns the verdict.
  Verdict on_epoch(Epoch e, uint32_t now_ms);

  // Call every loop: queues the hourly heartbeat (once there is a position to report) and drives the uplink.
  void tick(uint32_t now_ms);

  bool mesh_present() const {
#ifdef STZ_NO_MESH
    return false;
#else
    return mesh_ != nullptr || mesh2_ != nullptr;
#endif
  }
  // Records built from synthetic epochs must never pass as live ones: in test mode every record is type `test`.
  void set_test_mode(bool on) { test_mode_ = on; }
  Tx& tx() { return tx_; }
  uint8_t current_class() const { return class_; }
  const Detector& detector() const { return detector_; }
  SubmitResult last_submit() const { return last_submit_; }
#ifndef STZ_NO_PER_SV
  // True once when the receiver should be restarted (see stz_recover.h); the detector has been told already. The
  // caller sends the UBX frame (ubx::build_cfg_rst) and re-enables the messages.
  bool take_receiver_restart() {
    bool r = restart_pending_;
    restart_pending_ = false;
    return r;
  }
  uint16_t receiver_restarts() const { return watch_.restarts(); }
#endif

 private:
  Detector detector_;
  Tx& tx_;
  mesh::Link* mesh_;
#ifndef STZ_NO_MESH
  mesh::Link* mesh2_;
  mesh::Fanout fanout_;
#endif
#ifndef STZ_NO_TIME_HOLD
  void hold_time(Epoch& e, uint32_t now_ms);
#endif

  Epoch last_epoch_;
  bool have_epoch_ = false;
  bool test_mode_ = false;
  uint8_t class_ = kNormal;       // the detector's current class
#ifndef STZ_NO_TIME_HOLD
  uint8_t sent_class_ = kNormal;  // the class of the last event or status change submitted
  bool change_pending_ = false;   // a class change not yet submitted (no valid time yet)
  uint32_t clock_utc_ = 0, clock_ms_ = 0;  // the last receiver time with a fix, and when it came
  bool have_clock_ = false;
#endif
#ifndef STZ_NO_PER_SV
  ReceiverWatch watch_{RecoverConfig{}};
  bool restart_pending_ = false;
#endif
  uint8_t last_conf_ = 0, last_drop_ = 0;  // of the latest examined epoch: a heartbeat during an event repeats them
  SubmitResult last_submit_ = SubmitResult::Queued;
};

}  // namespace stz
