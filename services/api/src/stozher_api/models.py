"""Database tables. Keep in step with migrations/versions."""

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Node(Base):
    """`id` is the uint32 node ID used in the packet crypto (docs/packet.md)."""

    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False, start=1), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))  # expert, tbeam, phone, sim
    mode: Mapped[str] = mapped_column(String(16), default="fixed")
    surveyed_position: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326), nullable=True)
    sky_mask: Mapped[Any] = mapped_column(JSONB, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_seen: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class NodeKey(Base):
    __tablename__ = "node_keys"

    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    key_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    master_key_enc: Mapped[bytes] = mapped_column(LargeBinary)  # never returned by the API
    valid_from: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class TransportId(Base):
    __tablename__ = "transport_ids"
    __table_args__ = (UniqueConstraint("transport", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    transport: Mapped[str] = mapped_column(String(16))  # kineis, mesh, direct, sim
    external_id: Mapped[str] = mapped_column(String(64))


class RawMessage(Base):
    """Append-only audit trail of every arrival, including rejected and duplicate ones."""

    __tablename__ = "raw_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transport: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    payload_hex: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    transport_meta: Mapped[Any] = mapped_column(JSONB, nullable=True)
    node_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verify_status: Mapped[str] = mapped_column(String(16))  # accepted, duplicate, rejected
    reject_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("node_id", "key_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    key_id: Mapped[int] = mapped_column(SmallInteger)
    seq: Mapped[int] = mapped_column(Integer)
    msg_type: Mapped[int] = mapped_column(SmallInteger)
    event_class: Mapped[int] = mapped_column(SmallInteger)
    confidence: Mapped[int] = mapped_column(SmallInteger)
    flags: Mapped[Any] = mapped_column(JSONB)
    position: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    observed_at: Mapped[datetime] = mapped_column(TS)
    duration_s: Mapped[int] = mapped_column(Integer)
    features: Mapped[Any] = mapped_column(JSONB)
    classifier: Mapped[Any] = mapped_column(JSONB, nullable=True)
    checks: Mapped[Any] = mapped_column(JSONB, nullable=True)
    origin: Mapped[str] = mapped_column(String(8), default="cloud")
    received_via: Mapped[str] = mapped_column(String(16))
    ingested_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    payload_hex: Mapped[str] = mapped_column(Text)
    sim: Mapped[bool] = mapped_column(Boolean, default=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(120))
    owner_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rate_limit_per_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allowed_origins: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Hotspot(Base):
    """Derived: recomputed by the worker, never synced."""

    __tablename__ = "hotspots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # jamming, spoofing
    state: Mapped[str] = mapped_column(String(16))  # active, closed
    centroid: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    source_ellipse: Mapped[Any] = mapped_column(Geometry("POLYGON", srid=4326))
    ellipse: Mapped[Any] = mapped_column(JSONB)  # semi_major_m, semi_minor_m, bearing_deg
    severity: Mapped[float] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    first_seen: Mapped[datetime] = mapped_column(TS)
    last_seen: Mapped[datetime] = mapped_column(TS)
    event_count: Mapped[int] = mapped_column(Integer)
    node_count: Mapped[int] = mapped_column(Integer)
    sim: Mapped[bool] = mapped_column(Boolean, default=False)
    track: Mapped[Any] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class HotspotEvent(Base):
    __tablename__ = "hotspot_events"

    hotspot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hotspots.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)


class TrustCell(Base):
    """Derived H3 (res 8) trust cell. Absent cell means UNKNOWN."""

    __tablename__ = "trust_cells"

    h3_index: Mapped[str] = mapped_column(String(16), primary_key=True)
    center: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    state: Mapped[str] = mapped_column(String(16))
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    confidence: Mapped[float] = mapped_column()
    independent_sources: Mapped[int] = mapped_column(Integer)
    source_kinds: Mapped[Any] = mapped_column(JSONB)  # coarse node kinds, never identities
    newest_observed_at: Mapped[datetime] = mapped_column(TS)
    sim: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Job(Base):
    """Postgres job queue (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[Any] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(String(16))  # pending, running, done, failed
    run_after: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    """Deterministic alerts raised by the worker; feeds the stream, webhooks and mesh text."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)  # stream cursor
    level: Mapped[str] = mapped_column(String(16))  # info, warning, critical
    kind: Mapped[str] = mapped_column(String(32))  # hotspot_new, hotspot_escalated, hotspot_cleared
    area: Mapped[Any] = mapped_column(JSONB, nullable=True)  # GeoJSON geometry
    message: Mapped[str] = mapped_column(Text)
    hotspot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    delivered: Mapped[Any] = mapped_column(JSONB, default=dict)
    sim: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(80), unique=True)


class AckRecord(Base):
    """A cloud-signed acknowledgement (docs/control.md). Reused while the node's state is unchanged."""

    __tablename__ = "acks"
    __table_args__ = (UniqueConstraint("node_id", "key_id", "cloud_key_id", "max_seq", "mask"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    key_id: Mapped[int] = mapped_column(SmallInteger)
    cloud_key_id: Mapped[int] = mapped_column(SmallInteger)
    max_seq: Mapped[int] = mapped_column(Integer)
    mask: Mapped[int] = mapped_column(Integer)
    signed_at: Mapped[datetime] = mapped_column(TS)
    signature: Mapped[bytes] = mapped_column(LargeBinary)
