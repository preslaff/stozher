#include "stz_node.h"

#ifndef STZ_NO_MESH
#include "stz_mesh.h"
#endif

namespace stz {

Node::Node(const DetectConfig& cfg, Tx& tx, mesh::Link* mesh, mesh::Link* mesh2)
    : detector_(cfg), tx_(tx), mesh_(mesh)
#ifndef STZ_NO_MESH
      , mesh2_(mesh2)
#endif
{
#ifndef STZ_NO_MESH
  if (mesh_ && mesh2_) {
    fanout_.add(mesh_);
    fanout_.add(mesh2_);
    tx_.set_sink(&fanout_);
  } else if (mesh_ || mesh2_) {
    tx_.set_sink(mesh_ ? mesh_ : mesh2_);
  }
#else
  (void)mesh2;
#endif
}

Verdict Node::on_epoch(Epoch e, uint32_t now_ms) {
  e.blanked = tx_.blanked(now_ms);
  Verdict v = detector_.update(e);
#ifndef STZ_NO_PER_SV
  if (!v.blanked && watch_.update(e, detector_.band_clean(e)) == RecoverAction::Restart) {
    detector_.receiver_restarted();
    restart_pending_ = true;
  }
#endif
  if (!v.blanked) {
    last_conf_ = v.confidence;
    last_drop_ = v.cn0_drop_db;
  }
#ifndef STZ_NO_TIME_HOLD
  hold_time(e, now_ms);  // after the detector: its clock rule (S4) compares only the receiver's own times
#endif
  if (!e.blanked && e.fix_ok && e.utc_s != 0) {  // a heartbeat reports the last place we really had a fix
    last_epoch_ = e;
    have_epoch_ = true;
  }
#ifndef STZ_NO_TIME_HOLD
  if (v.changed && !v.blanked) {
    class_ = v.event_class;
    change_pending_ = true;
  }
  // A record never leaves with timestamp 0 (the cloud rejects it): a change seen before the first time since boot
  // waits for one, and is dropped if the class is back to what was last sent by then.
  if (change_pending_ && !v.blanked && e.utc_s != 0) {
    change_pending_ = false;
    if (class_ != sent_class_) {
      Record r;
      fill_record(v, e, r);
      r.mesh = mesh_present();
      if (test_mode_) r.type = kTest;
      last_submit_ = tx_.submit(class_ != kNormal ? TxKind::Event : TxKind::Status, r, now_ms);
      sent_class_ = class_;
    }
  }
#else
  // No room on the Leonardo (18 bytes short) for the clock carry-over or this guard: its u-blox PVT gives fix and
  // time together, so a change without a time is rare there, and the heartbeat guard above still applies.
  if (v.changed && !v.blanked) {
    Record r;
    fill_record(v, e, r);
    r.mesh = mesh_present();
    if (test_mode_) r.type = kTest;
    last_submit_ = tx_.submit(v.event_class != kNormal ? TxKind::Event : TxKind::Status, r, now_ms);
    class_ = v.event_class;
  }
#endif
  return v;
}

#ifndef STZ_NO_TIME_HOLD
void Node::hold_time(Epoch& e, uint32_t now_ms) {
  // Seen on a T-Beam (Oct 2026): an epoch with a valid fix but no valid NMEA date/time, so utc_s 0. The node's
  // own clock carries the last good time over such gaps (drift is seconds a day).
  if (e.utc_s != 0 && e.fix_ok) {
    clock_utc_ = e.utc_s;
    clock_ms_ = now_ms;
    have_clock_ = true;
  } else if (e.utc_s == 0 && have_clock_) {
    e.utc_s = clock_utc_ + (uint32_t)(now_ms - clock_ms_) / 1000u;
  }
}
#endif

void Node::tick(uint32_t now_ms) {
  if (have_epoch_ && tx_.heartbeat_due(now_ms)) {
    Verdict v;  // during an event the heartbeat repeats the event's confidence and drop
    v.event_class = class_;
    if (class_ != kNormal) {
      v.confidence = last_conf_;
      v.cn0_drop_db = last_drop_;
    }
    Record r;
    fill_record(v, last_epoch_, r);
    r.type = kHeartbeat;
    r.mesh = mesh_present();
    if (test_mode_) r.type = kTest;
    tx_.submit(TxKind::Heartbeat, r, now_ms);
  }
  tx_.poll(now_ms);
#ifndef STZ_NO_MESH
  mesh::Link* links[2] = {mesh_, mesh2_};
  for (mesh::Link* link : links) {
    if (!link) continue;
    mesh::PollResult m = link->poll(now_ms);
    if (m.sent) tx_.note_mesh_tx(now_ms, link->config().airtime_ms);  // our own LoRa burst is not evidence
    if (m.ack) tx_.on_ack(m.ack_data);  // Tx passes it on to every link through the sink
  }
#endif
}

}  // namespace stz
