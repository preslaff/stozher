import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from .app import run_bridge
from .meshtastic_radio import parse_tcp_port
from .radio import FakeRadio, Radio
from .sim_radio import SimTcpRadio
from .sim_radio import parse_port as parse_sim_port

DEFAULT_PORT = "/dev/ttyUSB0"


def _bridge_name(v: str) -> str | None:
    if not v:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,15}", v) or v == "main":
        raise argparse.ArgumentTypeError(
            "lowercase letters, digits, - and _, at most 16, not 'main' (that is the unnamed bridge)"
        )
    return v


def _radio_factory(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    if args.port == "fake":
        return lambda: FakeRadio()
    sim = parse_sim_port(args.port)
    if sim is not None:
        return lambda: SimTcpRadio(*sim)
    from .meshtastic_radio import MeshtasticRadio  # needs the optional `radio` extra

    return lambda: MeshtasticRadio(args.port)


def _open(args: argparse.Namespace) -> Radio:
    """Connect for the one-shot commands, with a readable message when the port is unusable."""
    radio: Radio = _radio_factory(args)()
    try:
        radio.start(lambda m: None)
    except PermissionError:
        sys.exit(
            f"No permission to open {args.port}. Add yourself to the dialout group, or for this session: "
            f"sudo setfacl -m u:$USER:rw {args.port}"
        )
    except OSError as exc:
        sys.exit(
            f"Cannot open {args.port}: {exc}. Is the gateway plugged in (or reachable on the network), and is nothing else "
            "(a serial monitor, the Meshtastic app) using it?"
        )
    return radio


def _need_radio_extra() -> None:
    try:
        import meshtastic  # noqa: F401
    except ImportError:
        sys.exit(
            "The Meshtastic library is not installed. Use: uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge ..."
        )


def cmd_run(args: argparse.Namespace) -> int:
    key = os.environ.get("STOZHER_SYSTEM_KEY", "")
    if not key:
        print(
            "set STOZHER_SYSTEM_KEY (a key with the system role; see docs/api.md section 3)",
            file=sys.stderr,
        )
        return 2
    try:
        sim = parse_sim_port(args.port) is not None
        parse_tcp_port(args.port)
    except ValueError as exc:
        print(f"--port: {exc}", file=sys.stderr)
        return 2
    if args.port != "fake" and not sim:
        _need_radio_extra()
    spool = None if args.no_spool else Path(args.spool).expanduser()
    try:
        asyncio.run(
            run_bridge(
                api_url=args.api,
                system_key=key,
                radio_factory=_radio_factory(args),
                spool=spool,
                responder_key=os.environ.get("STOZHER_RESPONDER_KEY") or None,
                relay_sim=args.relay_sim,
                relay_interval_s=args.relay_interval,
                send_acks=not args.no_acks,
                health_port=args.health_port,
                name=args.name,
                sim=sim,
                stale_after_s=0 if sim else args.stale_after,
            )
        )
    except KeyboardInterrupt:
        pass
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Print this radio's node ID and the nodes it has heard. Node IDs are the mesh transport IDs."""
    _need_radio_extra()
    radio = _open(args)
    try:
        print(f"this radio: {radio.node_id}")
        for n in radio.known_nodes():
            print(f"  {n['id']}  {n['short_name']}  {n['long_name']}  {n['hw']}  snr={n['snr']}")
        print(
            "Register a witness node with transport 'mesh' and external_id = its node ID (e.g. !a1b2c3d4)."
        )
    finally:
        radio.close()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    """Send one text over the radio (for tests, e.g. a STZ1:<hex> record from a second T-Beam)."""
    _need_radio_extra()
    radio = _open(args)
    try:
        radio.send_text(args.text, args.channel)
        print(f"sent {len(args.text)} characters on channel {args.channel} from {radio.node_id}")
    finally:
        radio.close()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="mesh_bridge", description="Meshtastic gateway bridge for STOZHER"
    )
    ap.add_argument("--api", default=os.environ.get("STOZHER_API_URL", "http://localhost:8000"))
    ap.add_argument(
        "--port",
        default=os.environ.get("MESH_PORT", DEFAULT_PORT),
        help="the gateway: its serial device (/dev/serial/by-id/...), tcp:<host>[:<port>] for a gateway on the "
        "network (Meshtastic's API over Wi-Fi, no authentication: private networks only), 'fake' for no hardware, or "
        "sim-tcp:127.0.0.1:<port> for a simulated radio (records then go to /v1/ingest/sim)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser(
        "run", help="forward mesh records to the API (and optionally alerts to the mesh)"
    )
    r.add_argument(
        "--spool", default="~/.local/state/stozher/mesh-spool.jsonl", help="store-and-forward file"
    )
    r.add_argument("--no-spool", action="store_true")
    r.add_argument(
        "--no-acks",
        action="store_true",
        help="do not send cloud-signed acknowledgements back to nodes",
    )
    r.add_argument(
        "--relay-sim",
        action="store_true",
        help="also send simulated alerts over the radio, marked [SIM]",
    )
    r.add_argument(
        "--relay-interval", type=float, default=60.0, help="minimum seconds between alert texts"
    )
    r.add_argument(
        "--health-port", type=int, default=None, help="serve JSON health on 127.0.0.1:<port>"
    )
    r.add_argument(
        "--stale-after",
        type=float,
        default=3 * 3600.0,
        help="reopen the radio link after this many seconds without a text (0: never); it is also reopened after the machine sleeps",
    )
    r.add_argument(
        "--name",
        type=_bridge_name,
        default=None,
        help="this bridge's name in /health when the kit runs more than one (e.g. 433 for a 433 MHz gateway)",
    )
    sub.add_parser("info", help="print this radio's node ID and the nodes it has heard")
    s = sub.add_parser("send", help="send a text over the radio")
    s.add_argument("text")
    s.add_argument("--channel", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )
    sys.exit({"run": cmd_run, "info": cmd_info, "send": cmd_send}[args.cmd](args))
