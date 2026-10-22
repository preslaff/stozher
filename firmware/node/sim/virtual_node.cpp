// A virtual dual-band witness node for the PC: the real node code (lib/stozher: detector, records, sequence numbers,
// mesh links, ack verification) with the hardware replaced. Each LoRa band is a TCP connection on this machine to a
// simulated gateway radio (services/mesh_bridge sim_radio.py); GNSS epochs are scripted (calm, a jamming episode, calm).
// Data only: nothing here touches a radio. Register the node as a SIM node; records arrive through /v1/ingest/sim.
// Build: make fw-sim. Run: python -m simulator dualband (docs/scenario.md, "Dual-band run").
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "stz_codec.h"
#include "stz_kim1.h"
#include "stz_mesh.h"
#include "stz_node.h"
#include "stz_tx.h"

using namespace stz;

namespace {

volatile sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

uint32_t mono_ms() {
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint32_t)(ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

// Deterministic per-band loss (xorshift), so a run with the same seed loses the same messages.
struct Rng {
  uint32_t s;
  explicit Rng(uint32_t seed) : s(seed ? seed : 1) {}
  double next() {
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    return (s & 0xFFFFFF) / (double)0x1000000;
  }
};

// One band: a TCP client to the simulated gateway radio. Lines written while the band is "down" (outage window or a
// random loss) count as sent by the radio but never arrive, as on air. With no connection the radio is unplugged:
// write() returns 0 and the link tries again later, as with a real dead UART.
class BandPort : public mesh::Port {
 public:
  BandPort(const char* name, int port, const char* sim_id, double drop, int out_from, int out_to, uint32_t seed)
      : name_(name), port_(port), sim_id_(sim_id), drop_(drop), out_from_(out_from), out_to_(out_to), rng_(seed) {}

  void set_elapsed_s(int s) { elapsed_s_ = s; }

  size_t write(const uint8_t* d, size_t n) override {
    if (!connected() && !connect_now()) return 0;
    bool outage = out_from_ >= 0 && elapsed_s_ >= out_from_ && elapsed_s_ < out_to_;
    if (outage || (drop_ > 0 && rng_.next() < drop_)) {
      lost++;
      return n;  // the radio took it; the air lost it
    }
    if (send(fd_, d, n, MSG_NOSIGNAL) != (ssize_t)n) {
      close_now();
      return 0;
    }
    delivered++;
    return n;
  }

  int read() override {
    if (!connected()) return -1;
    if (pos_ == len_) {
      pos_ = len_ = 0;
      ssize_t r = recv(fd_, buf_, sizeof buf_, MSG_DONTWAIT);
      if (r == 0 || (r < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
        close_now();
        return -1;
      }
      if (r <= 0) return -1;
      len_ = (size_t)r;
      acks_lines += count_lines(buf_, len_);
    }
    return buf_[pos_++];
  }

  bool connect_now() {
    uint32_t now = mono_ms();
    if (tried_ && (uint32_t)(now - last_try_ms_) < 1000) return false;
    tried_ = true;
    last_try_ms_ = now;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return false;
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons((uint16_t)port_);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (sockaddr*)&a, sizeof a) != 0) {
      close(fd);
      return false;
    }
    char hello[96];
    int m = snprintf(hello, sizeof hello, "SIMID:%s\n", sim_id_);
    if (send(fd, hello, (size_t)m, MSG_NOSIGNAL) != m) {
      close(fd);
      return false;
    }
    fd_ = fd;
    printf("[%s] connected to the simulated gateway on 127.0.0.1:%d as %s\n", name_, port_, sim_id_);
    fflush(stdout);
    return true;
  }

  bool connected() const { return fd_ >= 0; }
  const char* name() const { return name_; }
  unsigned delivered = 0, lost = 0, acks_lines = 0;

 private:
  static unsigned count_lines(const uint8_t* b, size_t n) {
    unsigned c = 0;
    for (size_t i = 0; i < n; i++) c += b[i] == '\n';
    return c;
  }
  void close_now() {
    if (fd_ >= 0) close(fd_);
    fd_ = -1;
  }
  const char* name_;
  int port_;
  const char* sim_id_;
  double drop_;
  int out_from_, out_to_;
  Rng rng_;
  int fd_ = -1;
  bool tried_ = false;
  uint32_t last_try_ms_ = 0;
  int elapsed_s_ = 0;
  uint8_t buf_[1024];
  size_t len_ = 0, pos_ = 0;
};

// Sequence numbers in a small file, so a rerun never reuses one (the API would reject it as a replay).
class FileSeqStore : public SeqStore {
 public:
  explicit FileSeqStore(const char* path) : path_(path) {}
  Result load(uint8_t key_id, uint16_t& next) override {
    FILE* f = fopen(path_, "r");
    if (!f) return Result::Missing;
    unsigned k = 0, v = 0;
    Result r = Result::Missing;
    while (fscanf(f, "%u %u", &k, &v) == 2)
      if (k == key_id) {
        next = (uint16_t)v;
        r = Result::Ok;
      }
    fclose(f);
    return r;
  }
  bool save(uint8_t key_id, uint16_t next) override {
    FILE* f = fopen(path_, "w");
    if (!f) return false;
    bool ok = fprintf(f, "%u %u\n", (unsigned)key_id, (unsigned)next) > 0;
    return fclose(f) == 0 && ok;
  }

 private:
  const char* path_;
};

struct Args {
  uint32_t node_id = 0;
  int port_868 = 47868, port_433 = 47433;
  const char* id_868 = nullptr;
  const char* id_433 = nullptr;
  int seconds = 300, heartbeat_s = 60, jam_at = 120, jam_for = 30;
  double drop_868 = 0, drop_433 = 0;
  int out868_from = -1, out868_to = -1, out433_from = -1, out433_to = -1;
  uint32_t seed = 1;
  const char* state = nullptr;
  const char* cloud_key_hex = nullptr;
  uint8_t cloud_key_id = 1;
  int32_t lat_e7 = 0, lon_e7 = 0;
};

[[noreturn]] void usage() {
  fprintf(stderr,
          "usage: virtual_node --node-id N --id-868 ID --id-433 ID --state FILE --cloud-key HEX --lat-e7 N --lon-e7 N\n"
          "  [--cloud-key-id 1] [--port-868 47868] [--port-433 47433] [--seconds 300] [--heartbeat-s 60]\n"
          "  [--jam-at 120] [--jam-for 30] [--drop-868 P] [--drop-433 P] [--outage-868 FROM:TO] [--outage-433 FROM:TO]\n"
          "  [--seed 1]\n");
  exit(2);
}

void window(const char* v, int& from, int& to) {
  if (sscanf(v, "%d:%d", &from, &to) != 2 || from < 0 || to <= from) usage();
}

Args parse(int argc, char** argv) {
  Args a;
  for (int i = 1; i < argc; i++) {
    const char* k = argv[i];
    if (i + 1 >= argc) usage();
    const char* v = argv[++i];
    if (!strcmp(k, "--node-id")) a.node_id = (uint32_t)strtoul(v, nullptr, 10);
    else if (!strcmp(k, "--port-868")) a.port_868 = atoi(v);
    else if (!strcmp(k, "--port-433")) a.port_433 = atoi(v);
    else if (!strcmp(k, "--id-868")) a.id_868 = v;
    else if (!strcmp(k, "--id-433")) a.id_433 = v;
    else if (!strcmp(k, "--seconds")) a.seconds = atoi(v);
    else if (!strcmp(k, "--heartbeat-s")) a.heartbeat_s = atoi(v);
    else if (!strcmp(k, "--jam-at")) a.jam_at = atoi(v);
    else if (!strcmp(k, "--jam-for")) a.jam_for = atoi(v);
    else if (!strcmp(k, "--drop-868")) a.drop_868 = atof(v);
    else if (!strcmp(k, "--drop-433")) a.drop_433 = atof(v);
    else if (!strcmp(k, "--outage-868")) window(v, a.out868_from, a.out868_to);
    else if (!strcmp(k, "--outage-433")) window(v, a.out433_from, a.out433_to);
    else if (!strcmp(k, "--seed")) a.seed = (uint32_t)strtoul(v, nullptr, 10);
    else if (!strcmp(k, "--state")) a.state = v;
    else if (!strcmp(k, "--cloud-key")) a.cloud_key_hex = v;
    else if (!strcmp(k, "--cloud-key-id")) a.cloud_key_id = (uint8_t)atoi(v);
    else if (!strcmp(k, "--lat-e7")) a.lat_e7 = (int32_t)strtol(v, nullptr, 10);
    else if (!strcmp(k, "--lon-e7")) a.lon_e7 = (int32_t)strtol(v, nullptr, 10);
    else usage();
  }
  if (!a.node_id || !a.id_868 || !a.id_433 || !a.state || !a.cloud_key_hex || a.seconds <= 0 || a.heartbeat_s <= 0 ||
      a.drop_868 < 0 || a.drop_868 > 1 || a.drop_433 < 0 || a.drop_433 > 1)
    usage();
  return a;
}

}  // namespace

int main(int argc, char** argv) {
  Args a = parse(argc, argv);
  signal(SIGINT, on_signal);
  signal(SIGTERM, on_signal);

  uint8_t master[16];
  for (int i = 0; i < 16; i++) master[i] = (uint8_t)i;  // the PUBLIC test key: a SIM node, never a real one
  Keys keys;
  TxConfig tc;
  tc.satellite_enabled = false;  // no KIM1 in the simulation; the mesh carries everything
  tc.node_id = a.node_id;
  tc.key_id = 0;
  tc.heartbeat_interval_s = (uint32_t)a.heartbeat_s;
  derive_keys(master, a.node_id, tc.key_id, keys);

  CloudKey cloud[1];
  cloud[0].id = a.cloud_key_id;
  size_t kn = 0;
  if (from_hex(a.cloud_key_hex, strlen(a.cloud_key_hex), cloud[0].pub, 32, kn) != Status::Ok || kn != 32) usage();

  kim1::NullPort no_kim;
  FileSeqStore store(a.state);
  Tx tx(tc, keys, no_kim, store);
  BandPort p868("868", a.port_868, a.id_868, a.drop_868, a.out868_from, a.out868_to, a.seed);
  BandPort p433("433", a.port_433, a.id_433, a.drop_433, a.out433_from, a.out433_to, a.seed * 2654435761u);
  mesh::Link l868(mesh::MeshConfig{}, p868, cloud, 1);
  mesh::Link l433(mesh::MeshConfig{}, p433, cloud, 1);
  DetectConfig dc;
  dc.fixed_node = true;
  dc.surveyed_lat_e7 = a.lat_e7;
  dc.surveyed_lon_e7 = a.lon_e7;
  Node node(dc, tx, &l868, &l433);

  Epoch calm;
  calm.fix_ok = true;
  calm.lat_e7 = a.lat_e7;
  calm.lon_e7 = a.lon_e7;
  calm.sats_tracked = 12;
  calm.sats_used = 10;
  calm.cn0_mean = 42;
  calm.cn0_spread_x10 = 38;
  calm.jam_state = 1;
  calm.spoof_state = 1;
  calm.sensor_stationary = true;
  Epoch jam = calm;
  jam.cn0_mean = 25;
  jam.jam_ind = 40;  // the receiver's noise indicator rises: jamming, not loss of reception
  jam.jam_state = 2;

  printf("virtual node %u (SIM, public test key): %d s, heartbeat every %d s, jamming at %d s for %d s\n",
         (unsigned)a.node_id, a.seconds, a.heartbeat_s, a.jam_at, a.jam_for);
  fflush(stdout);
  p868.connect_now();
  p433.connect_now();

  uint32_t start = mono_ms();
  uint8_t last_class = kNormal;
  int last_second = -1;
  while (!g_stop) {
    uint32_t now = mono_ms();
    int elapsed = (int)((now - start) / 1000u);
    if (elapsed >= a.seconds) break;
    p868.set_elapsed_s(elapsed);
    p433.set_elapsed_s(elapsed);
    if (elapsed != last_second) {  // one epoch a second, stamped with the real UTC time
      last_second = elapsed;
      bool jamming = elapsed >= a.jam_at && elapsed < a.jam_at + a.jam_for;
      Epoch e = jamming ? jam : calm;
      e.utc_s = (uint32_t)time(nullptr);
      e.mono_ms = now;
      node.on_epoch(e, now);
      if (node.current_class() != last_class) {
        printf("t=%ds class %u -> %u\n", elapsed, (unsigned)last_class, (unsigned)node.current_class());
        fflush(stdout);
        last_class = node.current_class();
      }
    }
    node.tick(now);
    usleep(100000);
  }

  uint16_t next = 0;
  store.load(0, next);
  printf("summary: next_seq=%u acks_applied=%u band_868 delivered=%u lost=%u ack_lines=%u band_433 delivered=%u lost=%u "
         "ack_lines=%u\n",
         (unsigned)next, (unsigned)tx.acks_applied(), p868.delivered, p868.lost, p868.acks_lines, p433.delivered, p433.lost,
         p433.acks_lines);
  return 0;
}
