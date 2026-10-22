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
  if (!e.blanked && e.fix_ok) {  // a heartbeat reports the last place we really had a fix
    last_epoch_ = e;
    have_epoch_ = true;
  }
  if (v.changed && !v.blanked) {
    Record r;
    fill_record(v, e, r);
    r.mesh = mesh_present();
    if (test_mode_) r.type = kTest;
    last_submit_ = tx_.submit(v.event_class != kNormal ? TxKind::Event : TxKind::Status, r, now_ms);
    class_ = v.event_class;
  }
  return v;
}

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
