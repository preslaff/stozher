# STOZHER on the T-Beam (Meshtastic fork, experiment)

Instead of a separate XIAO node plus a stock-Meshtastic T-Beam, run the STOZHER logic inside the T-Beam's own
Meshtastic firmware. Analysis and plan: `docs/firmware.md`, section "T-Beam fork". Status: GPS tap and mesh module flashed and
run on a real T-Beam; the KIM1 option (below) only compiled.

What is here (the Meshtastic checkout itself is not vendored; it is GPL-3.0 and lives in its own git worktree):

- `src/StozherTap.{h,cpp}`: receives every byte of the GPS stream, picks the UBX NAV-SVINFO and MON-HW frames out of it
  with `stz::ubx::Reader` (the same code as the XIAO node) and, once per NAV-SVINFO, assembles one STOZHER `Epoch`
  from them plus the position, time and fix that Meshtastic's TinyGPS++ parsed from NMEA. For now it only logs.
- `patches/0001-stozher-gps-tap.patch`: three small edits to the Meshtastic checkout: the tap call in
  `GPS::whileActive()`, two CFG-MSG commands in the NEO-6 branch that turn on NAV-SVINFO and MON-HW, and a build
  environment `tbeam-stozher`. Everything is behind `-D STOZHER_TAP=1`.
- `apply.sh`: links `firmware/node/lib/{stozher,tweetnacl}` into the checkout's `lib/`, copies the tap, applies the patch.

```
git -C ../Meshtastic-firmware worktree add ../Meshtastic-firmware-stozher -b stozher   # once
firmware/tbeam/apply.sh ../Meshtastic-firmware-stozher
cd ../Meshtastic-firmware-stozher && pio run -e tbeam-stozher
```

Flashing a board is a separate, deliberate step (back up the flash first, see docs/firmware.md). The stock firmware
binaries in `firmware/node/` restore a board.

## Build options (all `-D` flags, e.g. `PLATFORMIO_BUILD_FLAGS="-DSTOZHER_NODE_ID=4242" pio run -e tbeam-stozher`)

| Flag | Meaning |
|---|---|
| `STOZHER_NODE_ID` | the node id registered in the backend (default 1); the master key is the PUBLIC test key until provisioning exists |
| `STOZHER_SELFTEST_AT_S=N` | N seconds after boot, feed a synthetic jamming episode; every record built from it is type `test` |
| `MESHTASTIC_EXCLUDE_BLUETOOTH=1` | Meshtastic's own flag (not ours): leave the Bluetooth stack out. Used on the test board after the stack asserted in `ld_acl.c` and rebooted it (Oct 2026). Image 1.86 MB instead of 2.08 MB. We use no phone app |
| `STOZHER_EPOCH_LOG_S=N` | log the detector's numbers (C/N0, drop, baseline, jamming indicator, class; no position) every N seconds, for tuning the rules on real data |
| `STOZHER_KIM1=1` | add the Kinéis KIM1 on UART2 (satellite path). **Never run on hardware yet.** Off: the pins are not touched |
| `STOZHER_KIM1_RX_PIN` / `_TX_PIN` / `_STATUS_PIN` | defaults 36 / 14 / 25. **Placeholders, not checked against the board** |
| `STOZHER_KIM1_BAND` / `_FREQ` / `_POWER_MW` | `AT+TX` parameters; placeholders `B1`, 300, 500 until the organisers give the real values |

KIM1 wiring notes: 5 V supply of its own with a bulk capacitor (the T-Beam's rail is not enough for a 1.5 W burst) and a
common ground; its UART is 3.3 V TTL like the ESP32, no level shifter. The Serial module of Meshtastic must not be
configured with pins (the module checks and leaves the KIM1 off if it is).
