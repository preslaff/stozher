"""The GnssLogger converter: features only, no position, records decode back."""

import importlib.util
from pathlib import Path

from stozher_core.codec import decode_hex
from stozher_core.crypto import derive_keys
from stozher_core.record import MsgType

spec = importlib.util.spec_from_file_location(
    "gnsslogger_convert", Path(__file__).parent.parent / "tools" / "gnsslogger" / "convert.py"
)
assert spec and spec.loader
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)

HEADER = (
    "# Raw,utcTimeMillis,Cn0DbHz,AgcDb,HardwareClockDiscontinuityCount\n"
    "# Status,UnixTimeMillis,ConstellationType,Svid,UsedInFix\n"
)


def make_log(tmp_path: Path, drop_in_second_window: float = 0.0) -> Path:
    lines = [HEADER.rstrip("\n")]
    lines.append(
        "Fix,GPS,12.3456789,45.6789012,10.0,1760000000000"
    )  # a position that must never reach the output
    for w in range(3):
        for k in range(12):
            t = 1_760_000_000_000 + w * 10_000 + k * 500
            cn0 = 30.0 + (k % 3) - (drop_in_second_window if w == 1 else 0.0)
            lines.append(f"Raw,{t},{cn0},-7.8,0")
        for sv in range(5):
            lines.append(f"Status,{1_760_000_000_000 + w * 10_000},1,{sv + 1},1")
    path = tmp_path / "log.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_windows_and_no_position(tmp_path: Path, capsys) -> None:
    path = make_log(tmp_path)
    ws = conv.read(str(path), 10)
    assert len(ws) == 3 and all(w.satellites == 5 and w.signals == 12 for w in ws)
    assert all(w.cn0_drop == 0 and not w.clock_discontinuity for w in ws)
    assert conv.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "12.3456789" not in out and "45.6789012" not in out


def test_drop_is_relative_to_the_file_median(tmp_path: Path) -> None:
    ws = conv.read(str(make_log(tmp_path, drop_in_second_window=9.0)), 10)
    assert [w.cn0_drop for w in ws] == [0, 9, 0]


def test_records_decode_as_test_without_position(tmp_path: Path) -> None:
    ws = conv.read(str(make_log(tmp_path)), 10)
    master = bytes(range(16))
    hexes = conv.to_hex(ws, 4242, master, first_seq=7)
    rec = decode_hex(hexes[0], 4242, derive_keys(master, 4242, 0))
    assert rec.type is MsgType.TEST and rec.seq == 7 and (rec.lat_e7, rec.lon_e7) == (0, 0)


def test_no_raw_lines_is_a_clear_error(tmp_path: Path) -> None:
    p = tmp_path / "empty.txt"
    p.write_text("# Version: x\n")
    try:
        conv.read(str(p))
    except SystemExit as e:
        assert "Raw" in str(e)
    else:
        raise AssertionError("expected SystemExit")
