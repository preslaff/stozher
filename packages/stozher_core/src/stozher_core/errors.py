"""Rejection reasons for witness records. Strings are part of the API contract."""

from enum import StrEnum


class Reason(StrEnum):
    BAD_HEX = "bad_hex"
    BAD_LENGTH = "bad_length"
    BAD_VERSION = "bad_version"
    BAD_TYPE = "bad_type"
    BAD_RESERVED = "bad_reserved"
    LENGTH_MISMATCH = "length_mismatch"
    BAD_TAG = "bad_tag"
    BAD_CLASS = "bad_class"
    BAD_COORDINATES = "bad_coordinates"


class PacketError(ValueError):
    """A witness record was rejected; `reason` is the stable code."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.value)
        self.reason = reason
