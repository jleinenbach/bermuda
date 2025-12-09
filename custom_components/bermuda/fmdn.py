"""Google Find My Device Network helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from .const import SERVICE_UUID_FMDN


def _normalize_service_uuid(service_uuid: str | int) -> str:
    """Return a lower-cased string for the provided UUID value."""
    if isinstance(service_uuid, int):
        return hex(service_uuid)
    return str(service_uuid).lower()


def is_fmdn_service_uuid(service_uuid: str | int) -> bool:
    """Return True if the uuid matches the FMDN service UUID."""
    normalized = _normalize_service_uuid(service_uuid)
    return normalized in {SERVICE_UUID_FMDN, "feaa", "0xfeaa", "0000feaa"}


def extract_fmdn_eid(service_data: Mapping[str | int, Any]) -> bytes | None:
    """Extract the ephemeral identifier from FMDN service data when present."""
    for service_uuid, payload in service_data.items():
        if not is_fmdn_service_uuid(service_uuid):
            continue

        if not isinstance(payload, (bytes, bytearray, memoryview)):
            continue
        if len(payload) < 2:
            continue

        frame_type = payload[0]
        if frame_type != 0x40:
            continue
        if len(payload) >= 21:
            return bytes(payload[1:21])

    return None
