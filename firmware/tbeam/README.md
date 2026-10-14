# STOZHER on the T-Beam (Meshtastic fork, experiment)

Instead of a separate XIAO node plus a stock-Meshtastic T-Beam, run the STOZHER logic inside the T-Beam's own
Meshtastic firmware. Analysis and plan: `docs/firmware.md`, section "T-Beam fork". Status: **step 2 only**, built,
not yet flashed.

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
