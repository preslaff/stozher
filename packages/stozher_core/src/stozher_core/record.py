"""Witness record schema (docs/packet.md)."""

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class MsgType(IntEnum):
    HEARTBEAT = 0
    EVENT = 1
    SUMMARY = 2
    TEST = 3


class EventClass(IntEnum):
    NORMAL = 0
    JAMMING = 1
    SPOOFING = 2
    BENIGN = 3
    TAMPER = 4
    TIME_ANOMALY = 5
    UNKNOWN = 7


class Osnma(IntEnum):
    NOT_AVAILABLE = 0
    OK = 1
    FAIL = 2
    UNKNOWN = 3


class Extended(BaseModel):
    """Fields only present in the 32-byte record. Raw codes, see docs/packet.md."""

    model_config = ConfigDict(frozen=True)

    satellites: int = Field(ge=0, le=255)
    pos_jump_code: int = Field(ge=0, le=255)
    clock_jump_code: int = Field(ge=0, le=255)
    battery: int = Field(ge=0, le=255)


class WitnessRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: MsgType
    seq: int = Field(ge=0, le=0xFFFF)
    key_id: int = Field(ge=0, le=15)
    event_class: EventClass
    confidence: int = Field(ge=0, le=31)
    osnma: Osnma
    stationary: bool
    rx_jamming: bool
    rx_spoofing: bool
    tamper: bool
    mesh: bool
    lat_e7: int = Field(ge=-900_000_000, le=900_000_000)
    lon_e7: int = Field(ge=-1_800_000_000, le=1_800_000_000)
    timestamp: int = Field(ge=0, le=0xFFFFFFFF)
    duration_s: int = Field(ge=0, le=0xFFFF)
    cn0_mean: int = Field(ge=0, le=255)
    cn0_drop: int = Field(ge=0, le=255)
    cn0_spread: int = Field(ge=0, le=255)
    agc: int = Field(ge=0, le=255)
    extended: Extended | None = None

    @property
    def lat(self) -> float:
        return self.lat_e7 / 1e7

    @property
    def lon(self) -> float:
        return self.lon_e7 / 1e7
