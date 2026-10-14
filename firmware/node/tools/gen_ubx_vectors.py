#!/usr/bin/env python3
# ruff: noqa: UP031  (printf-style keeps the C++ row templates readable)
"""Generate test/test_ubx/ubx_vectors.h: u-blox UBX frames built by pyubx2 (an independent implementation of the
u-blox message layouts), with the values the C++ parser must read back from them.

pyubx2 (BSD-3-Clause) is a development tool only; it is not part of the firmware. Run `make fw-vectors` to refresh.
`tests/test_firmware_vectors.py` fails if the committed header is stale.
"""

import calendar
import json
import math
import sys
from pathlib import Path

from pyubx2 import GET, UBXMessage

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "firmware/node/test/test_ubx/ubx_vectors.h"


def pvt(name, **f):
    base = dict(
        iTOW=1000,
        year=2026,
        month=11,
        day=27,
        hour=10,
        min=30,
        second=5,
        validDate=1,
        validTime=1,
        fullyResolved=1,
        tAcc=20,
        nano=-500,
        fixType=3,
        gnssFixOk=1,
        numSV=17,
        lon=27.0,
        lat=42.5,
        height=1000,
        hMSL=900,
        hAcc=1500,
        vAcc=2000,
        gSpeed=120,
    )
    base.update(f)
    m = UBXMessage("NAV", "NAV-PVT", GET, **base)
    unix = calendar.timegm(
        (base["year"], base["month"], base["day"], base["hour"], base["min"], base["second"])
    )
    if not (base["validDate"] and base["validTime"]):
        unix = 0  # the receiver has no valid UTC yet
    return dict(
        name=name,
        hex=m.serialize().hex(),
        itow=base["iTOW"],
        year=base["year"],
        month=base["month"],
        day=base["day"],
        hour=base["hour"],
        min=base["min"],
        sec=base["second"],
        fix_type=base["fixType"],
        fix_ok=base["gnssFixOk"],
        num_sv=base["numSV"],
        lat=round(base["lat"] * 1e7),
        lon=round(base["lon"] * 1e7),
        h_acc=base["hAcc"],
        g_speed=base["gSpeed"],
        nano=base["nano"],
        t_acc=base["tAcc"],
        unix=unix,
    )


def sat(name, svs, itow=1000):
    kw = dict(iTOW=itow, version=1, numSvs=len(svs))
    for i, (gnss, sv, cno, used) in enumerate(svs, 1):
        kw.update(
            {
                f"gnssId_{i:02d}": gnss,
                f"svId_{i:02d}": sv,
                f"cno_{i:02d}": cno,
                f"elev_{i:02d}": 30,
                f"azim_{i:02d}": 100,
                f"prRes_{i:02d}": 0,
                f"qualityInd_{i:02d}": 4 if cno else 0,
                f"svUsed_{i:02d}": used,
                f"health_{i:02d}": 1,
            }
        )
    m = UBXMessage("NAV", "NAV-SAT", GET, **kw)
    n, used, mean, spread10 = _stats(name, svs)
    return dict(
        name=name,
        hex=m.serialize().hex(),
        itow=itow,
        tracked=n,
        used=used,
        mean=mean,
        spread10=spread10,
    )


def _stats(name, svs):
    tracked = [c for _, _, c, _ in svs if c > 0]
    n = len(tracked)
    mean_exact = sum(tracked) / n if n else 0.0
    mean = (2 * sum(tracked) + n) // (2 * n) if n else 0
    var = sum(c * c for c in tracked) / n - mean_exact**2 if n else 0.0
    spread10 = 10 * math.sqrt(max(var, 0.0))
    assert abs((spread10 % 1) - 0.5) > 0.05, f"{name}: spread too close to a rounding boundary"
    used = sum(1 for _, _, c, u in svs if c > 0 and u)
    return n, used, mean, int(spread10 + 0.5)


def svinfo(name, svs, itow=1000):
    """NAV-SVINFO (the NEO-6 / u-blox 6 satellite message), same expected summary as NAV-SAT."""
    kw = dict(iTOW=itow, numCh=len(svs), chipGen=1, reserved1=0)
    for i, (_, sv, cno, used) in enumerate(svs, 1):
        kw.update(
            {
                f"chn_{i:02d}": i - 1,
                f"svid_{i:02d}": sv,
                f"svUsed_{i:02d}": used,
                f"qualityInd_{i:02d}": 7 if cno else 0,
                f"cno_{i:02d}": cno,
                f"elev_{i:02d}": 30,
                f"azim_{i:02d}": 100,
                f"prRes_{i:02d}": 0,
            }
        )
    m = UBXMessage("NAV", "NAV-SVINFO", GET, **kw)
    n, used, mean, spread10 = _stats(name, svs)
    return dict(
        name=name,
        hex=m.serialize().hex(),
        itow=itow,
        tracked=n,
        used=used,
        mean=mean,
        spread10=spread10,
    )


def _frame(cls, mid, payload):
    body = bytes([cls, mid]) + len(payload).to_bytes(2, "little") + payload
    a = b = 0
    for x in body:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return (b"\xb5\x62" + body + bytes([a, b])).hex()


def hw(name, jam_state, noise, agc, jam_ind, long=False):
    """NAV MON-HW: 60 bytes by pyubx2 (u-blox 7/8 layout); `long` re-lays it out as the 68-byte u-blox 6 form
    (VP[25]) by hand, so that one is NOT independent of the C++ and must be confirmed on a real NEO-6."""
    z4 = b"\x00" * 4
    kw = dict(
        pinSel=z4,
        pinBank=z4,
        pinDir=z4,
        pinVal=z4,
        noisePerMS=noise,
        agcCnt=agc,
        aStatus=2,
        aPower=1,
    )
    kw.update(
        rtcCalib=1, safeBoot=0, jammingState=jam_state, xtalAbsent=0, reserved0=0, usedMask=z4
    )
    kw.update(jamInd=jam_ind, reserved1=0, pinIrq=z4, pullH=z4, pullL=z4)
    for i in range(1, 18):
        kw[f"VP_{i:02d}"] = b"\x00"
    payload = UBXMessage("MON", "MON-HW", GET, **kw).serialize()[6:-2]
    assert len(payload) == 60
    if long:
        payload = payload[:45] + b"\x00" * 8 + payload[45:]
    return dict(
        name=name,
        hex=_frame(0x0A, 0x09, payload),
        jam_state=jam_state,
        agc=agc,
        jam_ind=jam_ind,
        noise=noise,
    )


def status(name, spoof, itow=1000):
    m = UBXMessage("NAV", "NAV-STATUS", GET, iTOW=itow, gpsFix=3, gpsFixOk=1, spoofDetState=spoof)
    return dict(name=name, hex=m.serialize().hex(), itow=itow, spoof=spoof)


def rf(name, blocks):
    kw = dict(version=0, nBlocks=len(blocks), msgSource=0)
    for i, (jam_state, agc, jam_ind) in enumerate(blocks, 1):
        kw.update(
            {
                f"blockId_{i:02d}": i - 1,
                f"jammingState_{i:02d}": jam_state,
                f"antStatus_{i:02d}": 2,
                f"antPower_{i:02d}": 1,
                f"postStatus_{i:02d}": 0,
                f"noisePerMS_{i:02d}": 90,
                f"agcCnt_{i:02d}": agc,
                f"jamInd_{i:02d}": jam_ind,
            }
        )
    m = UBXMessage("MON", "MON-RF", GET, **kw)
    return dict(
        name=name,
        hex=m.serialize().hex(),
        jam_state=max(b[0] for b in blocks),
        agc=max(b[1] for b in blocks),
        jam_ind=max(b[2] for b in blocks),
        noise=90,
    )


BOOT_CONFIG = [
    ("CFG_UART1OUTPROT_NMEA", 0),
    ("CFG_UART1OUTPROT_UBX", 1),
    ("CFG_RATE_MEAS", 1000),
    ("CFG_MSGOUT_UBX_NAV_PVT_UART1", 1),
    ("CFG_MSGOUT_UBX_NAV_SAT_UART1", 1),
    ("CFG_MSGOUT_UBX_NAV_STATUS_UART1", 1),
    ("CFG_MSGOUT_UBX_MON_RF_UART1", 1),
]


def flip(hexstr, byte_index):
    b = bytearray(bytes.fromhex(hexstr))
    b[byte_index] ^= 0x01
    return b.hex()


def render() -> str:
    pvts = [
        pvt("pvt_fixed_node"),
        pvt("pvt_no_fix", fixType=0, gnssFixOk=0, numSV=0, validTime=0, hAcc=50000),
        pvt("pvt_southwest", lat=-33.9, lon=-70.6, nano=999000000, gSpeed=-5, second=59),
        pvt("pvt_next_epoch", iTOW=2000, second=6),
    ]
    sats = [
        sat("sat_two", [(2, 5, 40, 1), (0, 7, 0, 0)]),
        sat(
            "sat_twelve",
            [
                (2, 3, 44, 1),
                (2, 5, 41, 1),
                (2, 8, 38, 1),
                (2, 11, 45, 1),
                (0, 1, 36, 1),
                (0, 7, 33, 1),
                (0, 12, 30, 0),
                (6, 2, 0, 0),
                (3, 4, 35, 1),
                (3, 9, 29, 0),
                (5, 1, 0, 0),
                (2, 14, 42, 1),
            ],
        ),
        sat("sat_empty", []),
        sat(
            "sat_forty",
            [(i % 7, i + 1, 25 + (i * 7) % 20, i % 3 != 0) for i in range(40)],
        ),
        sat("sat_two_next", [(2, 5, 40, 1), (0, 7, 38, 1)], itow=2000),
    ]
    twelve = [
        (0, 3, 44, 1),
        (0, 5, 41, 1),
        (0, 8, 38, 1),
        (0, 11, 45, 1),
        (0, 1, 36, 1),
        (0, 7, 33, 1),
        (0, 12, 30, 0),
        (0, 2, 0, 0),
        (0, 4, 35, 1),
        (0, 9, 29, 0),
        (0, 6, 0, 0),
        (0, 14, 42, 1),
    ]
    svinfos = [
        svinfo("svinfo_two", [(0, 5, 38, 1), (0, 9, 0, 0)]),
        svinfo("svinfo_twelve", twelve),
        svinfo("svinfo_empty", []),
        svinfo("svinfo_forty", [(0, i + 1, 24 + (i * 5) % 17, i % 4 != 0) for i in range(40)]),
    ]
    hws = [
        hw("hw_clean", 1, 60, 2500, 12),
        hw("hw_warning", 2, 77, 3100, 140),
        hw("hw_68_bytes", 2, 120, 4000, 200, long=True),
    ]
    stats = [status(f"status_spoof_{s}", s) for s in range(4)]
    rfs = [rf("rf_ok", [(1, 3000, 12)]), rf("rf_two_blocks", [(2, 4000, 77), (3, 5000, 180)])]
    valset = UBXMessage.config_set(1, 0, BOOT_CONFIG).serialize().hex()
    good = pvts[0]["hex"]
    streams = [
        ("two_frames_back_to_back", sats[0]["hex"] + stats[2]["hex"], "ST"),
        ("junk_before_and_between", "00b5ff62" + good + "b5b5b5" + rfs[0]["hex"], "PR"),
        ("bad_checksum_is_dropped", flip(good, 20) + stats[1]["hex"], "XT"),
        ("oversized_length_resyncs", "b5620107ffff" + stats[1]["hex"], "T"),
        ("unknown_message_is_ignored", "b5620a04" + "0000" + "0e34" + stats[1]["hex"], "OT"),
    ]
    for _, h, _ in streams:
        bytes.fromhex(h)
    o = [
        "// GENERATED by firmware/node/tools/gen_ubx_vectors.py (frames from pyubx2). Do not edit.",
        "#pragma once",
        "#include <stdint.h>",
        "",
        "struct PvtVec { const char* name; const char* hex; uint32_t itow; uint16_t year; uint8_t month, day, hour, min, sec, fix_type, fix_ok, num_sv; int32_t lat_e7, lon_e7; uint32_t h_acc_mm; int32_t g_speed_mm_s, nano; uint32_t t_acc_ns, unix_s; };",
        "struct SatVec { const char* name; const char* hex; uint32_t itow; uint8_t tracked, used, mean, spread10; };",
        "struct StatusVec { const char* name; const char* hex; uint32_t itow; uint8_t spoof; };",
        "struct RfVec { const char* name; const char* hex; uint8_t jam_state; uint16_t agc; uint8_t jam_ind; uint16_t noise; };",
        "struct StreamVec { const char* name; const char* hex; const char* kinds; };",
        "",
        "static const PvtVec kPvt[] = {",
    ]
    for v in pvts:
        o.append(
            "  {%s, %s, %du, %d, %d, %d, %d, %d, %d, %d, %d, %d, %d, %d, %du, %d, %d, %du, %du},"
            % (
                json.dumps(v["name"]),
                json.dumps(v["hex"]),
                v["itow"],
                v["year"],
                v["month"],
                v["day"],
                v["hour"],
                v["min"],
                v["sec"],
                v["fix_type"],
                v["fix_ok"],
                v["num_sv"],
                v["lat"],
                v["lon"],
                v["h_acc"],
                v["g_speed"],
                v["nano"],
                v["t_acc"],
                v["unix"],
            )
        )
    o += ["};", "", "static const SatVec kSat[] = {"]
    for v in sats:
        o.append(
            "  {%s, %s, %du, %d, %d, %d, %d},"
            % (
                json.dumps(v["name"]),
                json.dumps(v["hex"]),
                v["itow"],
                v["tracked"],
                v["used"],
                v["mean"],
                v["spread10"],
            )
        )
    o += ["};", "", "static const SatVec kSvinfo[] = {"]
    for v in svinfos:
        o.append(
            "  {%s, %s, %du, %d, %d, %d, %d},"
            % (
                json.dumps(v["name"]),
                json.dumps(v["hex"]),
                v["itow"],
                v["tracked"],
                v["used"],
                v["mean"],
                v["spread10"],
            )
        )
    o += ["};", "", "static const StatusVec kStatus[] = {"]
    for v in stats:
        o.append(
            "  {%s, %s, %du, %d},"
            % (json.dumps(v["name"]), json.dumps(v["hex"]), v["itow"], v["spoof"])
        )
    o += ["};", "", "static const RfVec kRf[] = {"]
    for v in rfs:
        o.append(
            "  {%s, %s, %d, %d, %d, %d},"
            % (
                json.dumps(v["name"]),
                json.dumps(v["hex"]),
                v["jam_state"],
                v["agc"],
                v["jam_ind"],
                v["noise"],
            )
        )
    o += ["};", "", "static const RfVec kHw[] = {"]
    for v in hws:
        o.append(
            "  {%s, %s, %d, %d, %d, %d},"
            % (
                json.dumps(v["name"]),
                json.dumps(v["hex"]),
                v["jam_state"],
                v["agc"],
                v["jam_ind"],
                v["noise"],
            )
        )
    o += ["};", "", f"static const char kBootConfigHex[] = {json.dumps(valset)};", ""]
    o.append("static const StreamVec kStreams[] = {")
    for n, h, k in streams:
        o.append("  {%s, %s, %s}," % (json.dumps(n), json.dumps(h), json.dumps(k)))
    o += ["};", ""]
    return "\n".join(o)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else 1)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
