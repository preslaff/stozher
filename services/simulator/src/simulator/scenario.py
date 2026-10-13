"""Scenario file schema (YAML or JSON): nodes, attackers and a timeline, all in virtual seconds."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class NodeSpec(BaseModel):
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    kind: Literal["expert", "tbeam", "phone"] = "expert"
    link: Literal["kineis", "mesh", "direct"] = "kineis"
    extended: bool = False  # 32-byte record (7-digit Kinéis ID)


class JammerSpec(BaseModel):
    id: str
    start: float
    end: float
    eirp_dbm: float = 30.0
    kind: Literal["wideband", "chirp", "tone"] = "wideband"
    duty: float = Field(default=1.0, gt=0, le=1)
    path_loss_exp: float = 3.2
    # static: lat/lon; moving: a track of [t, lat, lon] waypoints (a vehicle on a road)
    lat: float | None = None
    lon: float | None = None
    track: list[tuple[float, float, float]] | None = None

    @model_validator(mode="after")
    def _where(self) -> "JammerSpec":
        if not self.track and (self.lat is None or self.lon is None):
            raise ValueError("jammer needs lat/lon or a track")
        return self


class SpooferSpec(BaseModel):
    id: str
    start: float
    end: float
    lat: float
    lon: float
    radius_m: float = 3000.0
    push_m: float = 400.0  # position offset reached at the end of the drag-off
    push_bearing_deg: float = 45.0
    time_push_ms: float = 0.0
    drag_off_s: float = 120.0


class BenignSpec(BaseModel):
    """A non-hostile degradation, e.g. ionospheric scintillation, at some nodes."""

    id: str
    start: float
    end: float
    nodes: list[str]
    drop_db: float = 7.0


class ForgeSpec(BaseModel):
    """Bad records that must be rejected: wrong key, flipped bit, or a resend that must dedupe."""

    at: float
    node: str
    mode: Literal["wrong_key", "bit_flip", "resend"]
    count: int = 1


class Scenario(BaseModel):
    name: str
    description: str = ""
    seed: int = 1
    duration_s: float
    epoch_s: float = 30.0
    heartbeat_s: float = 600.0
    event_period_s: float = 120.0
    nodes: list[NodeSpec]
    jammers: list[JammerSpec] = []
    spoofers: list[SpooferSpec] = []
    benign: list[BenignSpec] = []
    forged: list[ForgeSpec] = []

    @model_validator(mode="after")
    def _refs(self) -> "Scenario":
        names = {n.name for n in self.nodes}
        if len(names) != len(self.nodes):
            raise ValueError("node names must be unique")
        for b in self.benign:
            if missing := set(b.nodes) - names:
                raise ValueError(f"benign {b.id}: unknown nodes {sorted(missing)}")
        for f in self.forged:
            if f.node not in names:
                raise ValueError(f"forged: unknown node {f.node}")
        return self


def load(name_or_path: str) -> Scenario:
    p = Path(name_or_path)
    if not p.exists():
        p = Path(__file__).parent / "scenarios" / f"{name_or_path}.yaml"
    return Scenario.model_validate(yaml.safe_load(p.read_text()))
