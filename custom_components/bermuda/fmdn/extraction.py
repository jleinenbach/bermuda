"""Google Find My Device Network helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from custom_components.bermuda.const import (
    _LOGGER,
    DEFAULT_FMDN_EID_FORMAT,
    FMDN_EID_CANDIDATE_LENGTHS,
    FMDN_EID_FORMAT_AUTO,
    FMDN_EID_FORMAT_STRIP_FRAME_20,
    FMDN_EID_FORMAT_STRIP_FRAME_ALL,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.log_spam_less import BermudaLogSpamLess

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

_LAST_MODE_LOGGED: list[str | None] = [None]
_LOG_SPAM_LESS = BermudaLogSpamLess(_LOGGER, spam_interval=300)
_FHN_UUID_MARKER = b"\xaa\xfe"  # 0xFEAA in little-endian order as it appears on-air
_FHN_FRAME_TYPES = (0x40, 0x41)


@dataclass(frozen=True)
class ExtractedEid:
    """Normalized EID payload parsed from Find Hub Network service data."""

    eid: bytes
    frame_type: int | None
    hashed_flags: int | None


def _normalize_service_uuid(service_uuid: str | int) -> str:
    """Return a lower-cased string for the provided UUID value."""
    if isinstance(service_uuid, int):
        return hex(service_uuid)
    return str(service_uuid).lower()


def is_fmdn_service_uuid(service_uuid: str | int) -> bool:
    """Return True if the uuid matches the FMDN service UUID."""
    normalized = _normalize_service_uuid(service_uuid)
    return normalized in {SERVICE_UUID_FMDN, "feaa", "0xfeaa", "0000feaa"}


def _log_mode(mode: str) -> None:
    """Log mode transitions to avoid repeated noisy debug output."""
    if mode != _LAST_MODE_LOGGED[0]:
        _LOGGER.debug("Using FMDN EID extraction mode: %s", mode)
        _LAST_MODE_LOGGED[0] = mode


def _log_malformed(mode: str, frame_type: int, payload_len: int, reason: str) -> None:
    """Log malformed payloads without spamming the logs."""
    _LOG_SPAM_LESS.debug(
        f"fmdn_malformed_{mode}_{frame_type:02x}_{payload_len}_{reason}",
        "Ignoring FMDN payload (mode=%s, frame=0x%02x, len=%d, reason=%s)",
        mode,
        frame_type,
        payload_len,
        reason,
    )


def _log_candidates(mode: str, frame_type: int, payload_len: int, count: int) -> None:
    """Log candidate extraction summaries without spamming."""
    _LOG_SPAM_LESS.debug(
        f"fmdn_candidates_{mode}_{frame_type:02x}_{payload_len}_{count}",
        "FMDN candidate extraction (mode=%s, frame=0x%02x, len=%d) yielded %d candidates",
        mode,
        frame_type,
        payload_len,
        count,
    )


def _normalized_mode(mode: str) -> str:
    """Return a supported extraction mode, falling back to the default."""
    if mode in {FMDN_EID_FORMAT_AUTO, FMDN_EID_FORMAT_STRIP_FRAME_ALL, FMDN_EID_FORMAT_STRIP_FRAME_20}:
        return mode
    _LOGGER.debug("Unknown FMDN EID format %s; defaulting to %s", mode, DEFAULT_FMDN_EID_FORMAT)
    return DEFAULT_FMDN_EID_FORMAT


def _sliding_window_candidates(payload: bytes, candidate_lengths: Sequence[int]) -> set[bytes]:
    """Generate sliding-window candidates across a payload."""
    candidates: set[bytes] = set()
    for length in candidate_lengths:
        if length > len(payload):
            continue
        for start in range(len(payload) - length + 1):
            candidates.add(bytes(payload[start : start + length]))
    return candidates


def _prefix_candidates(payload: bytes, candidate_lengths: Sequence[int]) -> set[bytes]:
    """Generate deterministic prefix candidates (payload[:len]) for each configured length."""
    candidates: set[bytes] = set()
    for length in candidate_lengths:
        if length <= 0:
            continue
        if len(payload) >= length:
            candidates.add(bytes(payload[:length]))
    return candidates


def _auto_trim_checksum_candidates(payload: bytes, candidate_lengths: Sequence[int]) -> set[bytes]:
    """
    In auto mode, consider trimming a trailing checksum byte.

    If payload length is exactly (candidate_length + 1), add payload[:-1].
    """
    candidates: set[bytes] = set()
    for length in candidate_lengths:
        if length <= 0:
            continue
        if len(payload) == length + 1:
            candidates.add(bytes(payload[:-1]))
    return candidates


def _extract_after_frame_type(payload: bytes, frame_type: int, start: int) -> ExtractedEid | None:
    """Extract an EID after the frame_type byte with optional hashed flags."""
    remaining = payload[start:]
    remaining_len = len(remaining)

    if remaining_len in (21, 33) and remaining_len - 1 in FMDN_EID_CANDIDATE_LENGTHS:
        eid_len = remaining_len - 1
        return ExtractedEid(eid=remaining[:eid_len], frame_type=frame_type, hashed_flags=remaining[-1])

    if remaining_len in FMDN_EID_CANDIDATE_LENGTHS:
        return ExtractedEid(eid=remaining, frame_type=frame_type, hashed_flags=None)

    return None


def _extract_embedded_uuid(payload: bytes) -> ExtractedEid | None:
    """Return an extracted EID when the FEAA UUID marker is embedded in the payload."""
    idx = payload.find(_FHN_UUID_MARKER)
    if idx == -1 or idx + 2 >= len(payload):
        return None

    candidate_frame_type = payload[idx + 2]
    if candidate_frame_type not in _FHN_FRAME_TYPES:
        return None

    return _extract_after_frame_type(payload, candidate_frame_type, start=idx + 3)


def _extract_eid_payload(payload: bytes) -> ExtractedEid | None:
    """
    Normalize a payload into a bare EID, accounting for optional frame and hashed flags bytes.

    Supported shapes:
    - EID only (20 or 32 bytes)
    - [frame_type] + EID
    - [frame_type] + EID + hashed_flags
    - payload containing ... 0xAA 0xFE [frame_type] [EID] [hashed_flags?]
    """
    if not payload:
        return None

    if len(payload) in FMDN_EID_CANDIDATE_LENGTHS:
        return ExtractedEid(eid=payload, frame_type=None, hashed_flags=None)

    if payload[0] in _FHN_FRAME_TYPES:
        extracted = _extract_after_frame_type(payload, frame_type=payload[0], start=1)
        if extracted:
            return extracted

    embedded = _extract_embedded_uuid(payload)
    if embedded:
        return embedded

    # Some sources may only append a hashed flag without a frame byte.
    if len(payload) in (21, 33) and len(payload) - 1 in FMDN_EID_CANDIDATE_LENGTHS:
        return ExtractedEid(eid=payload[:-1], frame_type=None, hashed_flags=payload[-1])

    return None


def _candidates_from_payload(
    payload: bytes,
    *,
    mode: str,
    candidate_lengths: Sequence[int],
) -> set[bytes]:
    """Produce a set of plausible EID candidates from a raw payload."""
    if not payload:
        return set()

    candidates: set[bytes] = set()
    extracted = _extract_eid_payload(payload)
    frame_type = (
        extracted.frame_type
        if extracted is not None and extracted.frame_type is not None
        else (payload[0] if payload else 0x00)
    )
    payload_len = len(payload)

    # Non-auto modes should be deterministic and cheap:
    # - strip_frame_20: first configured length from the normalized EID
    # - strip_frame_all: normalized EID and its prefixes
    # Auto mode may use broader heuristics (prefix + checksum-trim + sliding windows).

    if extracted is not None:
        base_eid = extracted.eid
        if mode == FMDN_EID_FORMAT_STRIP_FRAME_20:
            if candidate_lengths and len(base_eid) >= candidate_lengths[0]:
                candidates.add(bytes(base_eid[: candidate_lengths[0]]))
            else:
                _log_malformed(mode, frame_type, payload_len, "short_after_frame")
            if extracted.frame_type is None and len(base_eid) in candidate_lengths:
                candidates.add(bytes(base_eid))
        elif mode == FMDN_EID_FORMAT_STRIP_FRAME_ALL:
            if not base_eid:
                _log_malformed(mode, frame_type, payload_len, "no_payload_after_frame")
            else:
                candidates.add(bytes(base_eid))
                candidates.update(_prefix_candidates(base_eid, candidate_lengths))
        else:
            candidates.add(bytes(base_eid))
            candidates.update(_prefix_candidates(base_eid, candidate_lengths))
            candidates.update(_auto_trim_checksum_candidates(base_eid, candidate_lengths))
            candidates.update(_sliding_window_candidates(base_eid, candidate_lengths))
    else:
        _log_malformed(mode, frame_type, payload_len, "frame_type")
        if payload and payload[0] in _FHN_FRAME_TYPES:
            after_frame = payload[1:]
            if mode == FMDN_EID_FORMAT_STRIP_FRAME_20:
                if candidate_lengths and len(after_frame) >= candidate_lengths[0]:
                    candidates.add(bytes(after_frame[: candidate_lengths[0]]))
                else:
                    _log_malformed(mode, frame_type, payload_len, "short_after_frame")
            elif mode == FMDN_EID_FORMAT_STRIP_FRAME_ALL:
                if not after_frame:
                    _log_malformed(mode, frame_type, payload_len, "no_payload_after_frame")
                else:
                    candidates.add(bytes(after_frame))
                    candidates.update(_prefix_candidates(after_frame, candidate_lengths))
            else:
                candidates.update(_prefix_candidates(after_frame, candidate_lengths))
                candidates.update(_auto_trim_checksum_candidates(after_frame, candidate_lengths))
                candidates.update(_sliding_window_candidates(after_frame, candidate_lengths))
        elif mode == FMDN_EID_FORMAT_STRIP_FRAME_20:
            if candidate_lengths:
                if len(payload) >= candidate_lengths[0] + 1:
                    candidates.add(bytes(payload[1 : 1 + candidate_lengths[0]]))
                elif len(payload) >= candidate_lengths[0]:
                    candidates.add(bytes(payload[: candidate_lengths[0]]))
        elif mode == FMDN_EID_FORMAT_STRIP_FRAME_ALL:
            candidates.add(bytes(payload))
            candidates.update(_prefix_candidates(payload, candidate_lengths))
        else:
            candidates.update(_prefix_candidates(payload, candidate_lengths))
            candidates.update(_auto_trim_checksum_candidates(payload, candidate_lengths))
            candidates.update(_sliding_window_candidates(payload, candidate_lengths))

    if not candidates:
        _log_malformed(mode, frame_type, payload_len, "no_candidates")
        return set()

    _log_candidates(mode, frame_type, payload_len, len(candidates))
    return candidates


def extract_fmdn_eids(
    service_data: Mapping[str | int, Any],
    *,
    mode: str | None = None,
    candidate_lengths: Iterable[int] | None = None,
) -> set[bytes]:
    """
    Extract all plausible ephemeral identifier candidates from FMDN service data.

    Modes:
    - strip_frame_20: prefer windows after the frame byte using the first configured length.
    - strip_frame_all: prefer windows after the frame byte using all configured lengths.
    - auto: generate windows across payloads with and without the frame byte present.
    """
    mode_value = DEFAULT_FMDN_EID_FORMAT if mode is None else str(mode)
    mode = _normalized_mode(mode_value)
    _log_mode(mode)

    lengths: tuple[int, ...] = tuple(candidate_lengths or FMDN_EID_CANDIDATE_LENGTHS)
    candidates: set[bytes] = set()

    for service_uuid, payload in service_data.items():
        if not is_fmdn_service_uuid(service_uuid):
            continue
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            continue

        payload_bytes = bytes(payload)
        payload_len = len(payload_bytes)
        _LOGGER.debug("Evaluating FMDN payload len=%d for candidates", payload_len)

        candidates.update(_candidates_from_payload(payload_bytes, mode=mode, candidate_lengths=lengths))

    return candidates


def extract_fmdn_eid(service_data: Mapping[str | int, Any], mode: str | None = None) -> bytes | None:
    """
    Legacy helper returning the first extracted EID candidate, if any.

    Prefer :func:`extract_fmdn_eids` for multi-candidate extraction.
    """
    candidates = extract_fmdn_eids(service_data, mode=mode)
    if not candidates:
        return None
    return next(iter(candidates))
