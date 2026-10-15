"""Database tables. Keep in step with migrations/versions."""

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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
    prev_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # old secret during a rotation
    prev_hash_expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
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
    # docs/access.md: every credential belongs to an organisation, has a purpose and a kind; staff keys
    # are bound to a named account. Nullable only for keys that predate this (flagged in the inventory).
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id"), nullable=True
    )
    purpose: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    bootstrap: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # done in setup mode, by one administrator


class Organisation(Base):
    """An admitted organisation (docs/access.md section 4). Every credential belongs to one."""

    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(24))
    contact_name: Mapped[str] = mapped_column(
        String(120)
    )  # a role or person responsible for its credentials
    contact_address: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(16), default="proposed"
    )  # proposed, active, suspended, offboarded
    max_role: Mapped[str] = mapped_column(String(16), default="responder")
    agreement_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agreement_accepted_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(48))
    onboarded_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class StaffUser(Base):
    """A named platform staff account (admin or operator). The only people the platform names."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    display_name: Mapped[str] = mapped_column(String(120))
    work_email: Mapped[str] = mapped_column(String(200), unique=True)
    role: Mapped[str] = mapped_column(String(16))  # operator, admin
    status: Mapped[str] = mapped_column(String(16), default="active")  # active, disabled
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(48))
    disabled_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AdminRequest(Base):
    """A sensitive action waiting for a second administrator (two-person rule, docs/access.md section 7)."""

    __tablename__ = "admin_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(
        String(32)
    )  # create_key, activate_organisation, create_user, change_user_role
    payload: Mapped[Any] = mapped_column(JSONB)  # never contains a secret
    requested_by: Mapped[str] = mapped_column(String(48))  # "user:<id>" or "key:<prefix>"
    requested_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending, approved, rejected, cancelled, expired
    decided_by: Mapped[str | None] = mapped_column(String(48), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    result: Mapped[Any] = mapped_column(
        JSONB, nullable=True
    )  # what was created (ids, never a secret)


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
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


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


class WatchedRoute(Base):
    """A route someone asked us to watch (docs/api.md section 7). Holds no position of anyone."""

    __tablename__ = "watched_routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_prefix: Mapped[str] = mapped_column(String(16), index=True)  # the API key that created it
    name: Mapped[str] = mapped_column(String(120))
    coordinates: Mapped[Any] = mapped_column(JSONB)  # [[lon, lat], ...] exactly as submitted
    buffer_m: Mapped[int] = mapped_column(Integer)
    min_level: Mapped[str] = mapped_column(String(16))  # degraded, jammed, spoofed
    notify: Mapped[Any] = mapped_column(JSONB)
    asset_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)
    last_state: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # worst state last evaluated
    transitions: Mapped[int] = mapped_column(
        Integer, default=0
    )  # alerts raised so far (dedupe key)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    hazard_min_tier: Mapped[str] = mapped_column(String(16), default="corroborated")
    # hazards already alerted on this route: {hazard_id: {"p": passability, "n": alerts so far}}
    hazard_state: Mapped[Any] = mapped_column(JSONB, default=dict)


class HazardReport(Base):
    """One community report (docs/hazards.md section 5). Append-only.

    Deliberately holds no identity beyond the issuing key's public prefix: no name, contact, device
    id, IP address, device location, free text or photo. A test pins this column list.
    """

    __tablename__ = "hazard_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reporter_prefix: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    passability: Mapped[str] = mapped_column(String(24))
    geometry: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=4326)
    )  # a point or a short line
    observed_at: Mapped[datetime] = mapped_column(TS)  # the reporter's claim
    received_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    sim: Mapped[bool] = mapped_column(Boolean, default=False)
    sim_team: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )  # simulated reports only: the team they pretend to come from
    status: Mapped[str] = mapped_column(String(16), default="accepted")  # accepted, withdrawn
    hazard_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class RoadHazard(Base):
    """A merged hazard (derived by the worker; see the build order in docs/hazards.md)."""

    __tablename__ = "road_hazards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(24))
    passability: Mapped[str] = mapped_column(String(24))  # the most severe among current sources
    geometry: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=4326))
    tier: Mapped[str] = mapped_column(String(16), default="unverified")
    sources: Mapped[Any] = mapped_column(JSONB)
    first_seen: Mapped[datetime] = mapped_column(TS)
    last_confirmed: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    valid_until: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(
        String(16), default="active"
    )  # active, closed, expired, dismissed
    sim: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[Any] = mapped_column(
        JSONB, nullable=True
    )  # counts and scene dates, never reporter text
    decision: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # confirmed, closed, dismissed (human)
    decided_by: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # operator key prefix; never returned
    decision_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(
        TS, nullable=True
    )  # when it stopped being active
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class HazardHistory(Base):
    """Tier and status changes of a hazard, for its timeline. Holds no reporter identity."""

    __tablename__ = "hazard_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hazard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    event: Mapped[str] = mapped_column(String(16))  # created, tier, status, passability, decision
    value: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(16))  # community, operator, system


class ModuleHeartbeat(Base):
    """The last time an optional module said it was alive (health shows present or absent from this)."""

    __tablename__ = "module_heartbeats"

    module: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    info: Mapped[Any] = mapped_column(JSONB, nullable=True)


class Webhook(Base):
    """A consumer's push subscription (docs/api.md section 6). The secret is sealed with the KEK and shown once."""

    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text)
    events: Mapped[Any] = mapped_column(JSONB)  # alert, hotspot, trust_change, route
    bbox: Mapped[Any] = mapped_column(JSONB, nullable=True)  # [minLon, minLat, maxLon, maxLat]
    min_level: Mapped[str] = mapped_column(String(16), default="info")
    include_sim: Mapped[bool] = mapped_column(Boolean, default=False)
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[str] = mapped_column(String(32))
    last_alert_id: Mapped[int] = mapped_column(
        BigInteger, default=0
    )  # alerts up to this id are dealt with
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class WebhookDelivery(Base):
    """One alert for one webhook, with its retry state. The id is the delivery id the consumer sees."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "alert_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"))
    alert_id: Mapped[int] = mapped_column(BigInteger)
    event: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending, ok, failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TS)
    last_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
