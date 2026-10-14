#include "stz_node.h"

namespace stz {

Verdict Node::on_epoch(Epoch e, uint32_t now_ms) {
  e.blanked = tx_.blanked(now_ms);
  Verdict v = detector_.update(e);
  if (!e.blanked) {
    last_epoch_ = e;
    have_epoch_ = true;
  }
  if (v.changed && !v.blanked) {
    Record r;
    fill_record(v, e, r);
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
    tx_.submit(TxKind::Heartbeat, r, now_ms);
  }
  tx_.poll(now_ms);
}

}  // namespace stz
