#include "stz_node.h"

#ifndef STZ_NO_MESH
#include "stz_mesh.h"
#endif

namespace stz {

Node::Node(const DetectConfig& cfg, Tx& tx, mesh::Link* mesh) : detector_(cfg), tx_(tx), mesh_(mesh) {
#ifndef STZ_NO_MESH
  if (mesh_) tx_.set_sink(mesh_);
#endif
}

Verdict Node::on_epoch(Epoch e, uint32_t now_ms) {
  e.blanked = tx_.blanked(now_ms);
  Verdict v = detector_.update(e);
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
    Verdict v;
    v.event_class = class_;
    Record r;
    fill_record(v, last_epoch_, r);
    r.type = kHeartbeat;
    r.mesh = mesh_present();
    if (test_mode_) r.type = kTest;
    tx_.submit(TxKind::Heartbeat, r, now_ms);
  }
  tx_.poll(now_ms);
#ifndef STZ_NO_MESH
  if (mesh_) {
    mesh::PollResult m = mesh_->poll(now_ms);
    if (m.sent) tx_.note_mesh_tx(now_ms, mesh_->config().airtime_ms);  // our own LoRa burst is not evidence
    if (m.ack) tx_.on_ack(m.ack_data);
  }
#endif
}

}  // namespace stz
