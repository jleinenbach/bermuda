"""
Area selection logic for Bermuda BLE tracker.

This module contains all area/room selection algorithms including:
- UKF (Unscented Kalman Filter) fingerprint matching
- Min-distance heuristic fallback
- Virtual distance calculation for scannerless rooms
- Cross-floor protection and streak logic
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from bluetooth_data_tools import monotonic_time_coarse

from .area_selection_helpers import AdvertAnalyzer
from .const import (
    ABSOLUTE_Z_SCORE_MAX,
    AREA_MAX_AD_AGE_DEFAULT,
    AREA_MAX_AD_AGE_LIMIT,
    AUTO_LEARNING_MAX_RSSI_VARIANCE,
    AUTO_LEARNING_MAX_VELOCITY,
    AUTO_LEARNING_MIN_CONFIDENCE,
    CONF_MAX_RADIUS,
    CONF_REFERENCE_TRACKERS,
    CONF_USE_PHYSICAL_RSSI_PRIORITY,
    CONFIDENCE_WINNER_MARGIN,
    CONFIDENCE_WINNER_MIN,
    CORR_CONFIDENCE_WINNER_MARGIN,
    CORR_CONFIDENCE_WINNER_MIN,
    CORRELATION_Z_PENALTY_BASE,
    CORRELATION_Z_PENALTY_OFFSET,
    CROSS_FLOOR_ESCAPE_BASE,
    CROSS_FLOOR_MARGIN_BASE,
    CROSS_FLOOR_MIN_HISTORY,
    CROSS_FLOOR_STREAK,
    DEFAULT_MAX_RADIUS,
    DEFAULT_USE_PHYSICAL_RSSI_PRIORITY,
    DISTANCE_INFINITE_SENTINEL,
    DISTANCE_TIE_THRESHOLD,
    EVIDENCE_WINDOW_SECONDS,
    FLOOR_DISTANCE_RATIO_THRESHOLD,
    FLOOR_ESCAPE_CAP_80,
    FLOOR_ESCAPE_CAP_85,
    FLOOR_ESCAPE_CAP_90,
    FLOOR_ESCAPE_CAP_95,
    FLOOR_IMBALANCE_MARGIN,
    FLOOR_MARGIN_CAP_60,
    FLOOR_MARGIN_CAP_70,
    FLOOR_MARGIN_CAP_75,
    FLOOR_MARGIN_CAP_80,
    FLOOR_RATIO_MARGIN,
    FLOOR_SANDWICH_MARGIN_BASE,
    FLOOR_SANDWICH_MARGIN_INCREMENT,
    FLOOR_SKIP_MARGIN,
    FLOOR_WITNESS_MARGIN_INCREMENT,
    HISTORY_WINDOW,
    INCUMBENT_MARGIN_METERS,
    MARGIN_STATIONARY_METERS,
    MATURE_ABSOLUTE_MIN_COUNT,
    MATURE_PROFILE_MIN_PAIRS,
    MINDIST_PENDING_IMPROVEMENT,
    MINDIST_SIGNIFICANT_IMPROVEMENT,
    MOVEMENT_STATE_MOVING,
    MOVEMENT_STATE_SETTLING,
    MOVEMENT_STATE_STATIONARY,
    NEAR_FIELD_ABS_WIN_METERS,
    NEAR_FIELD_CUTOFF,
    NEAR_FIELD_THRESHOLD,
    PDIFF_HISTORICAL,
    PDIFF_OUTRIGHT,
    REFERENCE_TRACKER_CONFIDENCE,
    REFERENCE_TRACKER_DEVICE_PREFIX,
    ROOM_AMBIGUITY_MAX_DIFF,
    ROOM_AMBIGUITY_MIN_SCORE,
    RSSI_CONSISTENCY_MARGIN_DB,
    RSSI_FALLBACK_CROSS_FLOOR_MARGIN,
    RSSI_FALLBACK_MARGIN,
    RSSI_INVALID_SENTINEL,
    SAME_FLOOR_MIN_HISTORY,
    SAME_FLOOR_STREAK,
    SCANNER_ACTIVITY_TIMEOUT,
    SCANNER_ALGO_TIMEOUT,
    SCANNER_RECOVERY_GRACE_SECONDS,
    SOFT_INC_MIN_DISTANCE_ADVANTAGE,
    SOFT_INC_MIN_HISTORY_DIVISOR,
    STABILITY_SIGMA_MOVING,
    STABILITY_SIGMA_SETTLING,
    STABILITY_SIGMA_STATIONARY,
    STREAK_LOW_CONFIDENCE_THRESHOLD,
    UKF_HIGH_CONFIDENCE_OVERRIDE,
    UKF_LOW_CONFIDENCE_THRESHOLD,
    UKF_MIN_MATCH_SCORE,
    UKF_MIN_RSSI_VARIANCE,
    UKF_MIN_SCANNERS,
    UKF_PROXIMITY_THRESHOLD_METERS,
    UKF_RETENTION_THRESHOLD,
    UKF_RSSI_SANITY_MARGIN,
    UKF_RSSI_SIGMA_MULTIPLIER,
    UKF_STICKINESS_BONUS,
    UKF_WEAK_SCANNER_MIN_DISTANCE,
    UPDATE_INTERVAL,
    VIRTUAL_DISTANCE_MIN_SCORE,
    VIRTUAL_DISTANCE_SCALE,
)
from .correlation import AreaProfile, AutoLearningStats, RoomProfile, z_scores_to_confidence
from .filters import UnscentedKalmanFilter

if TYPE_CHECKING:
    from homeassistant.helpers.area_registry import AreaRegistry

    from .bermuda_advert import BermudaAdvert
    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ScannerOnlineStatus:
    """
    Online/offline state of a scanner for algorithm decisions.

    This is separate from the binary_sensor UI timeout (30s). The algorithm
    timeout (120s) is more lenient because algorithmic decisions tolerate brief
    gaps better, while the UI should react quickly to outages.

    Uses last_seen_by_any_device (max advert stamp across ALL devices) to avoid
    BUG 22 resurgence: in quiet rooms without BLE traffic, scanner.last_seen
    doesn't update even though the scanner is online and functional.
    """

    address: str
    area_id: str | None = None
    last_seen_by_any_device: float = 0.0
    is_online: bool = True
    went_offline_at: float | None = None
    came_online_at: float | None = None


@dataclass
class AreaTests:
    """
    Diagnostic information for area selection decisions.

    This dataclass captures comprehensive diagnostic data from both the UKF
    fingerprint matching path and the min-distance heuristic fallback. It enables
    users and developers to understand WHY a particular room was selected.

    The data is exposed via:
    - `sensortext()`: Compact string for the sensor's native_value (max 255 chars)
    - `to_dict()`: Structured dict for sensor's extra_state_attributes
    - `__str__()`: Verbose format for debug logging
    """

    # === IDENTITY ===
    device: str = ""

    # === DECISION PATH ===
    # Which algorithm made the final decision
    decision_path: str = "UNKNOWN"
    # Values: "UKF", "MIN_DISTANCE", "VIRTUAL", "LOCKED", "RESCUE", "UNKNOWN"

    # === AREA TRANSITION (existing, kept for compatibility) ===
    scannername: tuple[str, str] = ("", "")  # (incumbent_scanner, challenger_scanner)
    areas: tuple[str, str] = ("", "")  # (from_area, to_area)
    same_area: bool = False  # The challenger is in the same area as incumbent

    # === MIN-DISTANCE FIELDS (existing, kept for compatibility) ===
    pcnt_diff: float = 0  # Distance percentage difference
    last_ad_age: tuple[float, float] = (0, 0)  # Seconds since last *any* ad from scanner
    this_ad_age: tuple[float, float] = (0, 0)  # How old the *current* advert is
    distance: tuple[float, float] = (0, 0)  # (incumbent_distance, challenger_distance)
    hist_min_max: tuple[float, float] = (0, 0)  # Min/max distance from history
    floors: tuple[str | None, str | None] = (None, None)  # Floor names
    floor_levels: tuple[str | int | None, str | int | None] = (None, None)  # Floor levels

    # === UKF MATCHING (new) ===
    ukf_match_score: float | None = None  # Best area's fingerprint match score (0-1)
    ukf_current_area_score: float | None = None  # Current area's score (for comparison)
    ukf_retention_mode: bool = False  # True = using lower retention threshold (0.15)
    ukf_stickiness_applied: bool = False  # True = stickiness bonus prevented switch
    ukf_threshold_used: float | None = None  # Actual threshold: 0.15 (retention) or 0.30 (switch)

    # === FINGERPRINT PROFILE (new) ===
    profile_source: str = "NONE"  # "BUTTON_TRAINED", "AUTO_LEARNED", "MIXED", "NONE"
    profile_sample_count: int | None = None  # Number of samples in the profile
    profile_has_button: bool = False  # True if profile has button training

    # === SCANNERLESS ROOM (new) ===
    is_scannerless_room: bool = False  # True if winner has no physical scanner
    virtual_distance: float | None = None  # Calculated virtual distance from UKF score

    # === SANITY CHECKS (new) ===
    # None = not checked, True = passed, False = failed
    passed_proximity_check: bool | None = None  # BUG 14: Device close to different scanner
    passed_topological_check: bool | None = None  # BUG 21: Scanner exists on target floor
    passed_rssi_sanity: bool | None = None  # RSSI vs strongest visible scanner
    nearest_scanner_distance: float | None = None  # Distance to nearest scanner
    nearest_scanner_area: str | None = None  # Area of nearest scanner

    # === TIMING/STALENESS (new) ===
    winner_advert_age: float | None = None  # Seconds since winner's last advertisement

    # === TOP CANDIDATES (new) ===
    # List of top candidates for debugging: [{"area": str, "score": float, "type": str}, ...]
    top_candidates: list[dict[str, Any]] = field(default_factory=list)

    # === SCANNER OFFLINE STATUS (Phase 5) ===
    offline_scanners_count: int = 0  # Number of scanners currently offline
    offline_scanner_addrs: str = ""  # Comma-separated offline scanner addresses
    coverage_penalty_applied: float = 0.0  # UKF coverage penalty (0.0-1.0, 0=no penalty)
    auto_learning_blocked_offline: bool = False  # True if auto-learning was skipped due to offline

    # === RESULT ===
    reason: str | None = None  # Human-readable reason/result string

    def sensortext(self) -> str:
        """
        Return compact diagnostic text optimized for UI display.

        Format: "[PATH] reason | 📍from→to | metrics..."
        Maximum 255 characters (HA sensor limit).
        """
        parts: list[str] = []

        # 1. Decision path with emoji and reason
        path_indicator = {
            "UKF": "🎯",
            "MIN_DISTANCE": "📏",
            "VIRTUAL": "👻",
            "LOCKED": "🔒",
            "RESCUE": "🛟",
            "UNKNOWN": "❓",
        }.get(self.decision_path, "❓")

        reason_short = (self.reason or "pending")[:50]
        parts.append(f"{path_indicator}{self.decision_path}: {reason_short}")

        # 2. Area transition
        from_area = (self.areas[0] or "?")[:12]
        to_area = (self.areas[1] or "?")[:12]
        if from_area != to_area and self.areas[0]:
            parts.append(f"📍{from_area}→{to_area}")
        elif to_area != "?":
            parts.append(f"📍{to_area}")

        # 3. UKF-specific metrics
        if self.decision_path == "UKF" and self.ukf_match_score is not None:
            mode = "R" if self.ukf_retention_mode else "S"
            sticky = "+" if self.ukf_stickiness_applied else ""
            parts.append(f"UKF:{self.ukf_match_score:.2f}{sticky}({mode})")

        # 4. Distance info
        if self.is_scannerless_room and self.virtual_distance is not None:
            parts.append(f"Virt:{self.virtual_distance:.1f}m")
        elif self.distance[1] > 0:
            parts.append(f"Dist:{self.distance[1]:.1f}m({self.pcnt_diff:+.0%})")

        # 5. Profile info (compact)
        if self.profile_source != "NONE":
            src = "BTN" if self.profile_has_button else "AUTO"
            count = self.profile_sample_count or 0
            parts.append(f"Prof:{src}({count})")

        # 6. Offline scanner info (only show if relevant)
        if self.offline_scanners_count > 0:
            parts.append(f"Offline:{self.offline_scanners_count}")
            if self.coverage_penalty_applied > 0:
                parts.append(f"CovPen:{self.coverage_penalty_applied:.0%}")

        # 7. Sanity check failures (only show if failed)
        failures = []
        if self.passed_proximity_check is False:
            failures.append("PROX")
        if self.passed_topological_check is False:
            failures.append("TOPO")
        if self.passed_rssi_sanity is False:
            failures.append("RSSI")
        if failures:
            parts.append(f"⚠️{','.join(failures)}")

        # 7. Staleness warning (if significant)
        if self.winner_advert_age is not None and self.winner_advert_age > 10:
            parts.append(f"Age:{self.winner_advert_age:.0f}s")

        return " | ".join(parts)[:255]

    def to_dict(self) -> dict[str, Any]:
        """
        Return structured dictionary for sensor extra_state_attributes.

        This provides detailed diagnostic data for HA automations and debugging.
        """
        attrs: dict[str, Any] = {
            # Core decision info
            "decision_path": self.decision_path,
            "reason": self.reason,
            "from_area": self.areas[0] or None,
            "to_area": self.areas[1] or None,
            "same_area": self.same_area,
        }

        # UKF-specific attributes
        if self.ukf_match_score is not None:
            attrs["ukf_score"] = round(self.ukf_match_score, 3)
            if self.ukf_current_area_score is not None:
                attrs["ukf_current_score"] = round(self.ukf_current_area_score, 3)
            attrs["ukf_threshold"] = self.ukf_threshold_used
            attrs["ukf_retention_mode"] = self.ukf_retention_mode
            attrs["ukf_stickiness_applied"] = self.ukf_stickiness_applied

        # Fingerprint profile
        if self.profile_source != "NONE":
            attrs["profile_source"] = self.profile_source
            attrs["profile_samples"] = self.profile_sample_count
            attrs["profile_has_button_training"] = self.profile_has_button

        # Scannerless room
        attrs["is_scannerless_room"] = self.is_scannerless_room
        if self.virtual_distance is not None:
            attrs["virtual_distance_m"] = round(self.virtual_distance, 2)

        # Distance info (for min-distance path)
        if self.distance[0] > 0 or self.distance[1] > 0:
            attrs["distance_incumbent_m"] = round(self.distance[0], 2)
            attrs["distance_challenger_m"] = round(self.distance[1], 2)
            attrs["distance_diff_percent"] = round(self.pcnt_diff * 100, 1)

        # Sanity checks (only include if checked)
        if self.passed_proximity_check is not None:
            attrs["sanity_proximity_passed"] = self.passed_proximity_check
        if self.passed_topological_check is not None:
            attrs["sanity_topological_passed"] = self.passed_topological_check
        if self.passed_rssi_sanity is not None:
            attrs["sanity_rssi_passed"] = self.passed_rssi_sanity

        if self.nearest_scanner_distance is not None:
            attrs["nearest_scanner_m"] = round(self.nearest_scanner_distance, 2)
            attrs["nearest_scanner_area"] = self.nearest_scanner_area

        # Timing
        if self.winner_advert_age is not None:
            attrs["winner_advert_age_s"] = round(self.winner_advert_age, 1)

        # Top candidates (for debugging)
        if self.top_candidates:
            attrs["top_candidates"] = [
                {
                    "area": c.get("area"),
                    "score": round(c.get("score", 0), 3) if c.get("score") else None,
                    "distance": round(c.get("distance", 0), 2) if c.get("distance") else None,
                    "type": c.get("type"),
                }
                for c in self.top_candidates[:5]
            ]

        # Scanner offline status
        if self.offline_scanners_count > 0:
            attrs["offline_scanners_count"] = self.offline_scanners_count
            attrs["offline_scanner_addrs"] = self.offline_scanner_addrs
            if self.coverage_penalty_applied > 0:
                attrs["coverage_penalty"] = round(self.coverage_penalty_applied, 3)
            if self.auto_learning_blocked_offline:
                attrs["auto_learning_blocked_offline"] = True

        # Floor info
        if self.floors[0] or self.floors[1]:
            attrs["from_floor"] = self.floors[0]
            attrs["to_floor"] = self.floors[1]

        return attrs

    def __str__(self) -> str:
        """
        Create verbose string representation for debug logging.

        Shows all fields with clear formatting for log analysis.
        """
        lines = [f"AreaTests for {self.device}:"]
        lines.append(f"  Decision Path: {self.decision_path}")
        lines.append(f"  Reason: {self.reason}")
        lines.append(f"  Areas: {self.areas[0]} → {self.areas[1]}")

        if self.ukf_match_score is not None:
            lines.append(f"  UKF Score: {self.ukf_match_score:.3f} (threshold: {self.ukf_threshold_used})")
            lines.append(f"  UKF Retention: {self.ukf_retention_mode}, Stickiness: {self.ukf_stickiness_applied}")

        if self.is_scannerless_room:
            lines.append(f"  Scannerless Room: Yes, Virtual Distance: {self.virtual_distance:.2f}m")

        if self.profile_source != "NONE":
            lines.append(f"  Profile: {self.profile_source} ({self.profile_sample_count} samples)")

        if self.distance[0] > 0 or self.distance[1] > 0:
            lines.append(f"  Distance: {self.distance[0]:.2f}m → {self.distance[1]:.2f}m ({self.pcnt_diff:+.1%})")

        # Sanity checks
        checks = []
        if self.passed_proximity_check is not None:
            checks.append(f"Proximity:{'✓' if self.passed_proximity_check else '✗'}")
        if self.passed_topological_check is not None:
            checks.append(f"Topo:{'✓' if self.passed_topological_check else '✗'}")
        if self.passed_rssi_sanity is not None:
            checks.append(f"RSSI:{'✓' if self.passed_rssi_sanity else '✗'}")
        if checks:
            lines.append(f"  Sanity Checks: {', '.join(checks)}")

        if self.top_candidates:
            lines.append(f"  Top Candidates: {len(self.top_candidates)}")
            lines.extend(
                f"    - {c.get('area')}: score={c.get('score', 'N/A')}, type={c.get('type')}"
                for c in self.top_candidates[:3]
            )

        return "\n".join(lines)


@dataclass(slots=True)
class _ReferenceTrackerProxy:
    """
    Lightweight proxy for aggregated reference tracker data.

    Mimics the minimal BermudaDevice interface needed by
    _update_device_correlations() without creating a full BermudaDevice.
    """

    address: str  # "ref:<area_id>"
    name: str  # "Reference Tracker (<area_name>)"
    area_id: str | None = None
    area_changed_at: float = 0.0
    adverts: dict[str, Any] = field(default_factory=dict)

    def get_movement_state(self, *, stamp_now: float | None = None) -> str:
        """Always stationary — reference trackers don't move."""
        return MOVEMENT_STATE_STATIONARY

    def get_dwell_time(self, *, stamp_now: float | None = None) -> float:
        """Always long dwell — reference trackers are permanently placed."""
        return 86400.0  # 24 hours


class AreaSelectionHandler:
    """
    Handles all area/room selection logic for Bermuda devices.

    This class encapsulates the complex area selection algorithms including:
    - UKF fingerprint matching for learned room patterns
    - Min-distance heuristic for simple proximity-based selection
    - Virtual distance calculation for scannerless rooms
    - Cross-floor protection with streak requirements
    - Stability margins and hysteresis to prevent flickering
    """

    def __init__(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Initialize the area selection handler.

        Args:
        ----
            coordinator: The parent coordinator that owns device state and configuration.

        """
        self.coordinator = coordinator
        # Auto-learning diagnostic stats (resets on HA restart, not persisted)
        self._auto_learning_stats = AutoLearningStats()
        # Feature 1: Per-device storage for last advertisement timestamps
        # Used to detect genuinely new data vs re-reading cached RSSI values
        self._device_last_stamps: dict[str, dict[str, float]] = {}
        # Scanner online/offline status registry (Phase 0)
        # Tracks per-scanner online status using SCANNER_ALGO_TIMEOUT (120s).
        # Uses advert timestamps from ALL devices to avoid BUG 22 (quiet room false-offline).
        self._scanner_status: dict[str, ScannerOnlineStatus] = {}
        # Per-cycle cache of offline scanner addresses (computed once, used 4x per device)
        self._cycle_offline_addrs: frozenset[str] = frozenset()
        # Reference tracker diagnostic data (last aggregation results)
        self._last_ref_tracker_aggregation: dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]] = {}

    # =========================================================================
    # Property accessors for coordinator state
    # =========================================================================

    @property
    def options(self) -> dict[str, Any]:
        """Access coordinator options."""
        return self.coordinator.options

    @property
    def correlations(self) -> dict[str, dict[str, AreaProfile]]:
        """Access device-specific correlation profiles."""
        return self.coordinator.correlations

    @property
    def room_profiles(self) -> dict[str, RoomProfile]:
        """Access device-independent room profiles."""
        return self.coordinator.room_profiles

    @property
    def device_ukfs(self) -> dict[str, UnscentedKalmanFilter]:
        """Access per-device UKF states."""
        return self.coordinator.device_ukfs

    @property
    def _scanners(self) -> set[BermudaDevice]:
        """Access scanner set."""
        return self.coordinator._scanners

    @property
    def ar(self) -> AreaRegistry | None:
        """Access Home Assistant area registry."""
        return self.coordinator.ar

    @property
    def devices(self) -> dict[str, BermudaDevice]:
        """Access device dictionary."""
        return self.coordinator.devices

    # =========================================================================
    # Diagnostic methods
    # =========================================================================

    def get_auto_learning_diagnostics(self) -> dict[str, Any]:
        """
        Get diagnostic information about auto-learning.

        Returns statistics about the auto-learning process including:
        - Total updates performed vs skipped (due to minimum interval)
        - Skip ratio (percentage of attempts skipped)
        - Per-device breakdown

        Note: Stats reset on Home Assistant restart (not persisted).

        Returns
        -------
            Dictionary with auto-learning statistics for diagnostics output.

        """
        stats_dict = self._auto_learning_stats.to_dict()

        # Add detailed per-device stats (limited to most active devices)
        device_details: dict[str, dict[str, int]] = {}
        for device_addr, device_stats in self._auto_learning_stats._device_stats.items():
            total = device_stats["performed"] + device_stats["skipped"]
            if total >= 10:  # Only include devices with meaningful activity
                device_details[device_addr] = {
                    "performed": device_stats["performed"],
                    "skipped": device_stats["skipped"],
                    "total": total,
                }

        # Sort by total activity and limit to top 20
        sorted_devices = sorted(
            device_details.items(),
            key=lambda x: x[1]["total"],
            reverse=True,
        )[:20]

        stats_dict["device_breakdown"] = dict(sorted_devices)

        return stats_dict

    def reset_auto_learning_stats(self) -> None:
        """Reset auto-learning statistics to zero."""
        self._auto_learning_stats.reset()

    # =========================================================================
    # Scanner online/offline status (Phase 0)
    # =========================================================================

    def _update_scanner_online_status(self, nowstamp: float) -> None:
        """
        Update scanner online/offline status from all device adverts.

        Uses the maximum advert stamp across ALL devices per scanner to determine
        if the scanner is online. This avoids BUG 22 resurgence: in quiet rooms
        without BLE traffic, scanner.last_seen doesn't update even though the
        scanner is online and functional. By checking adverts from ALL tracked
        devices, a scanner is considered online as long as it sees ANY device.

        Uses SCANNER_ALGO_TIMEOUT (120s), not SCANNER_ACTIVITY_TIMEOUT (30s).
        """
        # Collect latest advert timestamp per scanner across ALL devices
        scanner_latest: dict[str, float] = {}
        for device in self.devices.values():
            for advert in device.adverts.values():
                if advert.scanner_address is not None and advert.stamp is not None:
                    prev = scanner_latest.get(advert.scanner_address, 0.0)
                    if advert.stamp > prev:
                        scanner_latest[advert.scanner_address] = advert.stamp

        # Update status for each registered scanner
        for scanner in self._scanners:
            addr = scanner.address
            status = self._scanner_status.get(addr)
            if status is None:
                status = ScannerOnlineStatus(
                    address=addr,
                    area_id=getattr(scanner, "area_id", None),
                )
                self._scanner_status[addr] = status

            status.area_id = getattr(scanner, "area_id", None)
            latest = scanner_latest.get(addr, 0.0)
            status.last_seen_by_any_device = max(status.last_seen_by_any_device, latest)

            was_online = status.is_online
            status.is_online = (
                status.last_seen_by_any_device > 0
                and (nowstamp - status.last_seen_by_any_device) < SCANNER_ALGO_TIMEOUT
            )

            # Track state transitions for recovery dampening (Phase 4)
            if was_online and not status.is_online:
                status.went_offline_at = nowstamp
                status.came_online_at = None
            elif not was_online and status.is_online:
                status.came_online_at = nowstamp

    def _get_offline_scanner_addrs(self) -> frozenset[str]:
        """Return addresses of scanners currently considered offline (algo timeout)."""
        return frozenset(addr for addr, status in self._scanner_status.items() if not status.is_online)

    def _is_scanner_recovering(self, scanner_addr: str, nowstamp: float) -> bool:
        """
        Check if a scanner recently came back online (within grace period).

        During recovery, the scanner's Kalman filter is cold and RSSI data may be
        unreliable. Used by Phase 4 to exclude recovering scanners from UKF matching.
        """
        status = self._scanner_status.get(scanner_addr)
        if status is None or status.came_online_at is None:
            return False
        return (nowstamp - status.came_online_at) < SCANNER_RECOVERY_GRACE_SECONDS

    def get_scanner_online_diagnostics(self) -> dict[str, Any]:
        """Return scanner online/offline status for diagnostics."""
        result: dict[str, Any] = {}
        for addr, status in self._scanner_status.items():
            result[addr] = {
                "is_online": status.is_online,
                "area_id": status.area_id,
                "last_seen_age_s": (
                    round(monotonic_time_coarse() - status.last_seen_by_any_device, 1)
                    if status.last_seen_by_any_device > 0
                    else None
                ),
                "went_offline_at_age_s": (
                    round(monotonic_time_coarse() - status.went_offline_at, 1)
                    if status.went_offline_at is not None
                    else None
                ),
                "came_online_at_age_s": (
                    round(monotonic_time_coarse() - status.came_online_at, 1)
                    if status.came_online_at is not None
                    else None
                ),
            }
        return result

    def get_reference_tracker_diagnostics(self) -> dict[str, Any]:
        """Return diagnostic info about reference tracker state."""
        configured = self.options.get(CONF_REFERENCE_TRACKERS, [])
        if not isinstance(configured, list):
            configured = []

        aggregation: dict[str, Any] = {}
        for area_id, (
            primary_rssi,
            primary_addr,
            other_readings,
            _stamps,
        ) in self._last_ref_tracker_aggregation.items():
            tracker_count = sum(
                1 for d in self.devices.values() if getattr(d, "is_reference_tracker", False) and d.area_id == area_id
            )
            aggregation[area_id] = {
                "tracker_count": tracker_count,
                "primary_scanner": primary_addr,
                "primary_rssi": round(primary_rssi, 1),
                "other_readings": {k: round(v, 1) for k, v in other_readings.items()},
            }

        return {
            "configured_count": len(configured),
            "configured_addresses": configured,
            "aggregation_by_area": aggregation,
        }

    # =========================================================================
    # Pure helper functions (no coordinator state access)
    # =========================================================================

    def _calculate_virtual_distance(self, score: float, max_radius: float) -> float:
        """
        Convert a UKF fingerprint match score to a virtual distance.

        Uses a scaled quadratic formula that rewards medium scores (0.3-0.5)
        more aggressively than linear, allowing scannerless rooms to compete
        against physical scanners through walls.

        Formula: max_radius * SCALE * (1 - score)²

        Args:
        ----
            score: UKF match score (0.0 to 1.0)
            max_radius: Maximum radius from configuration

        Returns:
        -------
            Virtual distance in meters. Lower scores produce larger distances.

        """
        score_clamped = max(VIRTUAL_DISTANCE_MIN_SCORE, min(1.0, score))
        return max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - score_clamped) ** 2)

    def _collect_current_stamps(self, device: BermudaDevice, nowstamp: float) -> dict[str, float]:
        """
        Collect current scanner timestamps from device adverts.

        This helper is used by BUG 20 fix to ensure streak counting only
        increments when NEW advertisement data arrives, preventing cached
        values from being counted multiple times.

        Args:
        ----
            device: The BermudaDevice to collect stamps from.
            nowstamp: Current monotonic timestamp for freshness check.

        Returns:
        -------
            Dictionary mapping scanner_address to advert timestamp.
            Only includes adverts within EVIDENCE_WINDOW_SECONDS.

        """
        current_stamps: dict[str, float] = {}
        for advert in device.adverts.values():
            if (
                advert.stamp is not None
                and advert.scanner_address is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            ):
                current_stamps[advert.scanner_address] = advert.stamp
        return current_stamps

    def _has_new_advert_data(self, current_stamps: dict[str, float], last_stamps: dict[str, float]) -> bool:
        """
        Check if any scanner has newer advertisement data.

        Compares current timestamps against previously recorded timestamps
        to detect when new BLE advertisement data has arrived.

        Args:
        ----
            current_stamps: Current scanner timestamps from _collect_current_stamps().
            last_stamps: Previously recorded timestamps (e.g., device.pending_last_stamps).

        Returns:
        -------
            True if at least one scanner has a newer timestamp.

        """
        return any(current_stamps.get(scanner, 0) > last_stamps.get(scanner, 0) for scanner in current_stamps)

    # =========================================================================
    # Registry helper functions (access ar, _scanners)
    # =========================================================================

    def _resolve_floor_id_for_area(self, area_id: str | None) -> str | None:
        """
        Resolve floor_id for an area_id using the Home Assistant area registry.

        This is essential for scannerless rooms where we can't get floor_id from
        a scanner_device - we must look up the area directly.
        """
        if area_id is None or self.ar is None:
            return None
        area = self.ar.async_get_area(area_id)
        if area is not None:
            return getattr(area, "floor_id", None)
        return None

    def _area_has_scanner(self, area_id: str) -> bool:
        """
        Check if an area has at least one scanner assigned to it.

        Args:
        ----
            area_id: The Home Assistant area ID to check.

        Returns:
        -------
            True if the area contains at least one scanner device.

        """
        return any(scanner.area_id == area_id for scanner in self._scanners)

    def _area_has_active_scanner(self, area_id: str, nowstamp: float) -> bool:
        """
        Check if an area has at least one ACTIVE scanner (recently seen).

        This is more strict than _area_has_scanner() - it also verifies that
        at least one scanner in the area has been active within the activity timeout.

        This distinction is important for BUG 22 fix: if a scanner is registered
        but offline (proxy rebooted, network loss), we should NOT reject UKF
        decisions for that area. Instead, treat it like a scannerless room and
        let UKF decide based on other scanners.

        NOTE: Uses SCANNER_ACTIVITY_TIMEOUT (30s) not EVIDENCE_WINDOW_SECONDS (15min)!
        This is intentional - we want to quickly detect scanner outages so we can
        fall back to UKF-based decisions rather than rejecting valid room selections
        for 15 minutes while waiting for evidence to expire.

        Args:
        ----
            area_id: The Home Assistant area ID to check.
            nowstamp: Current monotonic timestamp for freshness check.

        Returns:
        -------
            True if the area contains at least one scanner that has been
            seen within SCANNER_ACTIVITY_TIMEOUT (30 seconds).

        """
        for scanner in self._scanners:
            if scanner.area_id == area_id:
                # Check if this scanner is active (has recent data)
                scanner_last_seen = getattr(scanner, "last_seen", None)
                # NOTE: last_seen defaults to 0 for new scanners, so we must check
                # that it's > 0 to ensure the scanner has actually reported data.
                # Otherwise, during startup (nowstamp < 30s) or for newly-registered
                # scanners, we'd incorrectly treat them as "active".
                if (
                    scanner_last_seen is not None
                    and scanner_last_seen > 0
                    and nowstamp - scanner_last_seen < SCANNER_ACTIVITY_TIMEOUT
                ):
                    return True
        return False

    def resolve_area_name(self, area_id: str | None) -> str | None:
        """
        Given an area_id, return the current area name.

        Will return None if the area id does *not* resolve to a single
        known area name.
        """
        if area_id is None or self.ar is None:
            return None

        areas = self.ar.async_get_area(area_id)
        if hasattr(areas, "name"):
            return getattr(areas, "name", "invalid_area")
        return None

    def effective_distance(self, advert: BermudaAdvert | None, nowstamp: float) -> float | None:
        """
        Calculate the best available distance estimate for an advert.

        Rules:
        1) If advert.rssi_distance is present, prefer it (smoothed distance).
        2) If the advert is fresh and has historical distance samples, return the most recent
           historical value to preserve the last known proximity when smoothing yields None.
        3) Otherwise return None.
        """
        if advert is None:
            return None

        if advert.rssi_distance is not None:
            return advert.rssi_distance

        # Use the advert's adaptive timeout if available, otherwise fall back to default.
        max_age = getattr(advert, "adaptive_timeout", None) or AREA_MAX_AD_AGE_DEFAULT
        max_age = min(max_age, AREA_MAX_AD_AGE_LIMIT)

        if advert.stamp < nowstamp - max_age:
            return None

        hist_distances = [
            value for value in getattr(advert, "hist_distance_by_interval", []) if isinstance(value, int | float)
        ]
        if hist_distances:
            return hist_distances[0]

        return None

    # =========================================================================
    # Correlation confidence (accesses self.correlations)
    # =========================================================================

    def _get_correlation_confidence(  # noqa: PLR0911
        self,
        device_address: str,
        area_id: str,
        primary_rssi: float | None,
        current_readings: dict[str, float],
    ) -> float:
        """
        Calculate correlation confidence for a device in an area.

        Compares observed RSSI patterns against learned expectations.

        Args:
        ----
            device_address: The device's address.
            area_id: The area to check confidence for.
            primary_rssi: RSSI from the primary scanner.
            current_readings: Map of scanner_id to RSSI for all visible scanners.

        Returns:
        -------
            Confidence value 0.0-1.0. Returns 1.0 if no learned data exists.

        """
        if device_address not in self.correlations:
            return 1.0
        if area_id not in self.correlations[device_address]:
            return 1.0
        if primary_rssi is None:
            return 1.0

        profile = self.correlations[device_address][area_id]
        if profile.mature_correlation_count == 0:
            return 1.0

        z_scores = profile.get_z_scores(primary_rssi, current_readings)
        if not z_scores:
            return 1.0

        # FIX: Integrate absolute RSSI z-scores to detect far-field false positives.
        absolute_z_scores = profile.get_absolute_z_scores(current_readings)
        if absolute_z_scores:
            max_abs_z = max(z for _, z in absolute_z_scores)
            if max_abs_z > UKF_RSSI_SIGMA_MULTIPLIER:
                absolute_penalty: float = CORRELATION_Z_PENALTY_BASE ** (max_abs_z - CORRELATION_Z_PENALTY_OFFSET)
                delta_confidence: float = z_scores_to_confidence(z_scores)
                return float(delta_confidence * absolute_penalty)

        return z_scores_to_confidence(z_scores)

    def _is_signal_ambiguous(
        self,
        device: BermudaDevice,
        area_id: str,
        primary_rssi: float,
        primary_scanner_addr: str | None,
        other_readings: dict[str, float],
    ) -> bool:
        """
        Check if the current RSSI pattern is ambiguous between rooms.

        Returns True if another room's profile matches the current signal
        equally well, meaning the assignment is uncertain and we should
        not reinforce either profile.

        This implements Feature 6 (Ambiguity Check) and prevents the
        self-reinforcing feedback loop where a wrong room learns the
        same signal as the correct room.

        Two-layer check:
        1. AreaProfiles (device-specific): Uses z-score matching
        2. RoomProfiles (device-independent): Used as fallback when no
           AreaProfiles exist - compares match scores
        """
        all_readings = dict(other_readings)
        if primary_scanner_addr is not None:
            all_readings[primary_scanner_addr] = primary_rssi

        # Layer 1: Check device-specific AreaProfiles (existing behavior)
        if device.address in self.correlations:
            device_profiles = self.correlations[device.address]
            for other_area_id, other_profile in device_profiles.items():
                if other_area_id == area_id:
                    continue
                abs_z_scores = other_profile.get_absolute_z_scores(all_readings)
                if not abs_z_scores:
                    continue
                # If ALL z-scores for another room are below 2.0, the signal
                # is consistent with that room too → ambiguous assignment.
                max_z = max(abs(z) for _, z in abs_z_scores)
                if max_z < 2.0:
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Auto-learning skip for %s: ambiguous signal (AreaProfile for %s also matches, max_z=%.1f)",
                            device.name,
                            other_area_id,
                            max_z,
                        )
                    return True
            # AreaProfiles checked, not ambiguous
            return False

        # Layer 2: Check device-independent RoomProfiles (for new devices)
        # This is critical to prevent the self-reinforcing feedback loop
        # where wrong assignments corrupt RoomProfiles for ALL devices.
        target_profile = self.room_profiles.get(area_id) if self.room_profiles else None
        if target_profile is None:
            return False

        target_score = target_profile.get_match_score(all_readings)

        # Only check for ambiguity if target room has a decent match
        # and there are other RoomProfiles to compare against
        if target_score < ROOM_AMBIGUITY_MIN_SCORE or len(self.room_profiles) < 2:
            return False

        # Check if any other RoomProfile matches almost as well
        for other_area_id, room_profile in self.room_profiles.items():
            if other_area_id == area_id:
                continue

            other_score = room_profile.get_match_score(all_readings)

            # Ambiguous if: other room has good score AND is close to target
            if other_score >= ROOM_AMBIGUITY_MIN_SCORE and target_score - other_score < ROOM_AMBIGUITY_MAX_DIFF:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "Auto-learning skip for %s: ambiguous RoomProfile signal "
                        "(target %s score=%.2f, competitor %s score=%.2f, diff=%.2f < %.2f)",
                        device.name,
                        area_id,
                        target_score,
                        other_area_id,
                        other_score,
                        target_score - other_score,
                        ROOM_AMBIGUITY_MAX_DIFF,
                    )
                return True

        return False

    def _check_movement_state_for_learning(
        self,
        device: BermudaDevice,
        nowstamp: float | None,
    ) -> str | None:
        """
        Check if device movement state allows auto-learning.

        Returns None if learning is allowed, or a skip_reason string if not.
        Checks both area_changed_at == 0.0 (startup/first discovery)
        and movement state (MOVING/SETTLING).
        """
        if nowstamp is None:
            return None

        if device.area_changed_at == 0.0:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Auto-learning skip for %s: area_changed_at uninitialized (startup/first discovery)",
                    device.name,
                )
            return "uninitialized_dwell"

        movement_state = device.get_movement_state(stamp_now=nowstamp)
        if movement_state in (MOVEMENT_STATE_MOVING, MOVEMENT_STATE_SETTLING):
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Auto-learning skip for %s: movement state %s (not stationary)",
                    device.name,
                    movement_state,
                )
            return "not_stationary"

        return None

    def _update_device_correlations(  # noqa: C901, PLR0911
        self,
        device: BermudaDevice,
        area_id: str,
        primary_rssi: float,
        primary_scanner_addr: str | None,
        other_readings: dict[str, float],
        nowstamp: float | None = None,
        *,
        confidence: float | None = None,
    ) -> None:
        """
        Update device correlations for area learning.

        Used by both UKF and min-distance selection paths to maintain
        consistent correlation data.

        Quality Filters (Features 1, 3, 5, 6):
        - Feature 3: Skip if confidence < AUTO_LEARNING_MIN_CONFIDENCE
        - Feature 5: Skip if movement state is not STATIONARY (10+ min)
        - Feature 5: Skip if velocity > AUTO_LEARNING_MAX_VELOCITY
        - Feature 5: Skip if RSSI variance > AUTO_LEARNING_MAX_RSSI_VARIANCE
        - Feature 6: Skip if signal is ambiguous (matches another room)

        Args:
        ----
            device: The device being tracked.
            area_id: The area the device is currently in.
            primary_rssi: RSSI from the primary (strongest) scanner.
            primary_scanner_addr: Address of the primary scanner.
            other_readings: RSSI readings from other visible scanners.
            nowstamp: Current timestamp for minimum interval enforcement.
            confidence: Area assignment confidence (0.0-1.0). If None, filter is skipped.

        """
        if not other_readings:
            return

        # =====================================================================
        # Quality Filter: Feature 3 - Confidence Filter
        # Only learn from high-confidence area assignments to avoid polluting
        # fingerprints with noise from uncertain decisions.
        # =====================================================================
        if confidence is not None and confidence < AUTO_LEARNING_MIN_CONFIDENCE:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Auto-learning skip for %s: low confidence %.2f < %.2f",
                    device.name,
                    confidence,
                    AUTO_LEARNING_MIN_CONFIDENCE,
                )
            if nowstamp is not None:
                self._auto_learning_stats.record_update(
                    performed=False,
                    stamp=nowstamp,
                    device_address=device.address,
                    skip_reason="low_confidence",
                )
            return

        # =====================================================================
        # Quality Filter: Feature 5 - Movement State Guard
        # Only learn when the device is STATIONARY (10+ min in same room).
        # This replaces the previous 30s dwell time check, which was too
        # permissive and allowed the self-reinforcing misclassification loop:
        # wrong room → auto-learn → stronger wrong profile → more wrong room.
        # By requiring 10+ minutes of stable presence, transient misclassifications
        # can no longer poison fingerprint profiles.
        #
        # NOTE: get_movement_state() returns STATIONARY when area_changed_at == 0.0
        # (startup/first discovery) to prevent area-selection flapping. But for
        # auto-learning this is dangerous: the initial assignment may be wrong,
        # and we have no evidence of sustained presence. Block explicitly.
        # =====================================================================
        # Reference Tracker Proxy devices bypass the movement state check.
        # They are explicitly marked as stationary by the user (L-08).
        is_reference_device = device.address.startswith(REFERENCE_TRACKER_DEVICE_PREFIX)
        if not is_reference_device:
            skip_reason = self._check_movement_state_for_learning(device, nowstamp)
            if skip_reason is not None:
                if nowstamp is not None:
                    self._auto_learning_stats.record_update(
                        performed=False,
                        stamp=nowstamp,
                        device_address=device.address,
                        skip_reason=skip_reason,
                    )
                return

        # =====================================================================
        # Quality Filter: Feature 5 - Velocity Filter
        # Skip learning when device is moving rapidly (RSSI unstable).
        # =====================================================================
        max_velocity = self._get_device_max_velocity(device)
        if max_velocity > AUTO_LEARNING_MAX_VELOCITY:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Auto-learning skip for %s: velocity %.2f m/s > %.2f m/s",
                    device.name,
                    max_velocity,
                    AUTO_LEARNING_MAX_VELOCITY,
                )
            if nowstamp is not None:
                self._auto_learning_stats.record_update(
                    performed=False,
                    stamp=nowstamp,
                    device_address=device.address,
                    skip_reason="high_velocity",
                )
            return

        # =====================================================================
        # Quality Filter: Feature 5 - RSSI Variance Filter
        # Skip learning when RSSI is highly variable (interference, multipath).
        # =====================================================================
        avg_rssi_variance = self._get_device_rssi_variance(device)
        if avg_rssi_variance > AUTO_LEARNING_MAX_RSSI_VARIANCE:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Auto-learning skip for %s: RSSI variance %.1f dB² > %.1f dB²",
                    device.name,
                    avg_rssi_variance,
                    AUTO_LEARNING_MAX_RSSI_VARIANCE,
                )
            if nowstamp is not None:
                self._auto_learning_stats.record_update(
                    performed=False,
                    stamp=nowstamp,
                    device_address=device.address,
                    skip_reason="high_rssi_variance",
                )
            return

        # =====================================================================
        # Quality Filter: Feature 6 - Ambiguity Check
        # Skip learning when the current RSSI pattern matches ANOTHER room's
        # profile equally well. This breaks the self-reinforcing feedback loop
        # where a wrong room learns the same signal as the correct room.
        # =====================================================================
        if self._is_signal_ambiguous(device, area_id, primary_rssi, primary_scanner_addr, other_readings):
            if nowstamp is not None:
                self._auto_learning_stats.record_update(
                    performed=False,
                    stamp=nowstamp,
                    device_address=device.address,
                    skip_reason="ambiguous_signal",
                )
            return

        # =====================================================================
        # Quality Filter: Feature 7 - Scanner Completeness Guard
        # Skip learning when important scanners for the CURRENT area are offline.
        # Learning with incomplete scanner coverage would create fingerprints
        # that are biased toward the "without scanner X" pattern, corrupting
        # the profile. When the scanner comes back, the profile won't match.
        # =====================================================================
        offline_addrs_learn = self._cycle_offline_addrs
        if offline_addrs_learn:
            # Check if any trained scanner for this area is offline
            if device.address in self.correlations and area_id in self.correlations[device.address]:
                area_prof = self.correlations[device.address][area_id]
                trained_addrs = area_prof.trained_scanner_addresses
                offline_trained_learn = trained_addrs & offline_addrs_learn
                if offline_trained_learn:
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Auto-learning skip for %s: %d trained scanner(s) offline for area %s: %s",
                            device.name,
                            len(offline_trained_learn),
                            area_id,
                            list(offline_trained_learn),
                        )
                    if nowstamp is not None:
                        self._auto_learning_stats.record_update(
                            performed=False,
                            stamp=nowstamp,
                            device_address=device.address,
                            skip_reason="scanner_offline",
                        )
                    return

        # =====================================================================
        # All quality filters passed - proceed with learning
        # =====================================================================

        # =====================================================================
        # Feature 1: Collect current advertisement timestamps
        # Used to detect genuinely new data vs re-reading cached RSSI values
        # =====================================================================
        current_stamps: dict[str, float] | None
        last_stamps: dict[str, float] | None

        if is_reference_device:
            # Reference tracker proxies carry no adverts (data comes from median
            # aggregation of real tracker adverts).  Bypass Feature 1 new-data
            # check: the aggregation step already ensures freshness via
            # EVIDENCE_WINDOW_SECONDS.  The minimum-interval check inside
            # AreaProfile.update() still runs (keyed on _last_update_stamp).
            current_stamps = None
            last_stamps = None
        else:
            current_stamps = {}
            for advert in device.adverts.values():
                if advert.scanner_address is not None and advert.stamp is not None:
                    current_stamps[advert.scanner_address] = advert.stamp

            # Get last timestamps for this device (empty dict if first update)
            last_stamps = self._device_last_stamps.get(device.address, {})

        # Ensure device has correlation entry
        if device.address not in self.correlations:
            self.correlations[device.address] = {}

        # Ensure area has profile
        if area_id not in self.correlations[device.address]:
            self.correlations[device.address][area_id] = AreaProfile(
                area_id=area_id,
            )

        # Update device-specific profile (with Feature 1 new data check + minimum interval)
        area_update_performed = self.correlations[device.address][area_id].update(
            primary_rssi=primary_rssi,
            other_readings=other_readings,
            primary_scanner_addr=primary_scanner_addr,
            nowstamp=nowstamp,
            last_stamps=last_stamps,
            current_stamps=current_stamps,
        )

        # Update room-wide profile (only if AreaProfile update was performed)
        # BUG FIX: Both profiles should use consistent interval enforcement.
        # If AreaProfile rejects due to minimum interval, RoomProfile should also skip.
        # This ensures device-specific and device-independent profiles stay in sync.
        if area_update_performed:
            all_readings = dict(other_readings)
            if primary_scanner_addr is not None:
                all_readings[primary_scanner_addr] = primary_rssi

            if area_id not in self.room_profiles:
                self.room_profiles[area_id] = RoomProfile(area_id=area_id)
            self.room_profiles[area_id].update(
                all_readings,
                nowstamp=nowstamp,
                last_stamps=last_stamps,
                current_stamps=current_stamps,
            )

            # Store current stamps for next call (only on successful update).
            # Reference tracker proxies use None stamps (Feature 1 bypassed),
            # so skip storing to avoid polluting the cache.
            if current_stamps is not None:
                self._device_last_stamps[device.address] = current_stamps

        # Record stats for diagnostic purposes
        # Use area_update_performed as the primary indicator (both have the same interval logic)
        if nowstamp is not None:
            self._auto_learning_stats.record_update(
                performed=area_update_performed,
                stamp=nowstamp,
                device_address=device.address,
            )

    def _get_device_max_velocity(self, device: BermudaDevice) -> float:
        """
        Get the maximum recent velocity across all device adverts.

        Used by Feature 5: Velocity Filter to skip learning during rapid movement.

        Args:
        ----
            device: The device to check.

        Returns:
        -------
            Maximum absolute velocity in m/s from recent advert history.
            Returns 0.0 if no velocity data is available.

        """
        max_velocity = 0.0
        for advert in device.adverts.values():
            if advert.hist_velocity:
                # Get most recent velocity (index 0 is newest)
                recent_velocity = abs(advert.hist_velocity[0])
                max_velocity = max(max_velocity, recent_velocity)
        return max_velocity

    def _get_device_rssi_variance(self, device: BermudaDevice) -> float:
        """
        Get the average RSSI variance across all device adverts.

        Used by Feature 5: RSSI Variance Filter to skip learning during unstable signals.

        Args:
        ----
            device: The device to check.

        Returns:
        -------
            Average RSSI variance in dB² from Kalman filters.
            Returns 0.0 if no variance data is available.

        """
        total_variance = 0.0
        count = 0
        for advert in device.adverts.values():
            if advert.rssi_kalman.is_initialized:
                total_variance += advert.rssi_kalman.variance
                count += 1
        return total_variance / count if count > 0 else 0.0

    # =========================================================================
    # Virtual distance for scannerless rooms
    # =========================================================================

    def _get_virtual_distances_for_scannerless_rooms(
        self,
        device: BermudaDevice,
        rssi_readings: dict[str, float],
    ) -> dict[str, float]:
        """
        Calculate virtual distances for scannerless rooms based on UKF fingerprint match.

        When UKF score is below threshold for switching, scannerless rooms would normally
        be invisible to min-distance fallback (since they have no scanner to measure).
        This method calculates a "virtual distance" based on how well the current RSSI
        pattern matches the trained fingerprint, allowing scannerless rooms to compete.

        Only considers rooms that:
        1. Have been explicitly button-trained by the user
        2. Have no physical scanner in the room
        3. Have a minimum UKF score (to avoid phantom matches)

        Args:
        ----
            device: The device to calculate virtual distances for.
            rssi_readings: Current RSSI readings from all visible scanners.

        Returns:
        -------
            Dict mapping area_id to virtual distance (meters) for scannerless rooms.

        """
        from .filters.ukf import UnscentedKalmanFilter  # noqa: PLC0415

        virtual_distances: dict[str, float] = {}

        # Need device profiles to calculate fingerprint matches
        if device.address not in self.correlations:
            return virtual_distances

        device_profiles = self.correlations[device.address]
        max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)

        # Need minimum scanners for meaningful score calculation
        if len(rssi_readings) < UKF_MIN_SCANNERS:
            return virtual_distances

        # Get or create UKF for this device
        if device.address not in self.device_ukfs:
            self.device_ukfs[device.address] = UnscentedKalmanFilter()

        ukf = self.device_ukfs[device.address]

        # Update UKF with current readings before matching
        ukf.predict(dt=UPDATE_INTERVAL)
        ukf.update_multi(rssi_readings)

        # Get all matches from UKF (with coverage penalty for offline scanners)
        offline_addrs = self._cycle_offline_addrs
        matches = ukf.match_fingerprints(device_profiles, self.room_profiles, offline_addrs)

        # DEBUG logging
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Virtual distance check for %s: %d device_profiles, %d matches, "
                "rssi_readings=%s, ukf_scanners=%s, offline=%s",
                device.name,
                len(device_profiles),
                len(matches),
                list(rssi_readings.keys()),
                ukf.scanner_addresses,
                list(offline_addrs) if offline_addrs else "none",
            )
            for area_id, profile in device_profiles.items():
                has_btn = profile.has_button_training
                has_scanner = self._area_has_scanner(area_id)
                abs_details = []
                if hasattr(profile, "_absolute_profiles"):
                    for scanner_addr, abs_prof in profile._absolute_profiles.items():
                        abs_details.append(
                            f"{scanner_addr[-8:]}:btn={abs_prof.button_sample_count}/auto={abs_prof.auto_sample_count}"
                        )
                _LOGGER.debug(
                    "  Profile %s: has_button_training=%s, area_has_scanner=%s, abs_profiles=[%s]",
                    area_id,
                    has_btn,
                    has_scanner,
                    ", ".join(abs_details) if abs_details else "none",
                )

        for area_id, _d_squared, score, _cov_penalty in matches:
            # Only consider button-trained profiles (explicit user intent)
            area_profile = device_profiles.get(area_id)
            if area_profile is None or not area_profile.has_button_training:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "  Skipping %s: no button training (profile=%s, has_btn=%s)",
                        area_id,
                        area_profile is not None,
                        area_profile.has_button_training if area_profile else "N/A",
                    )
                continue

            # Only consider scannerless rooms (rooms with scanners use real distance)
            if self._area_has_scanner(area_id):
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug("  Skipping %s: area has scanner", area_id)
                continue

            # Minimum score threshold to avoid phantom matches
            if score < VIRTUAL_DISTANCE_MIN_SCORE:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug("  Skipping %s: score %.4f < min %.4f", area_id, score, VIRTUAL_DISTANCE_MIN_SCORE)
                continue

            # Calculate virtual distance using scaled quadratic formula
            virtual_dist = self._calculate_virtual_distance(score, max_radius)
            virtual_distances[area_id] = virtual_dist

            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Virtual distance for %s in scannerless room %s: score=%.3f → distance=%.2fm",
                    device.name,
                    area_id,
                    score,
                    virtual_dist,
                )

        return virtual_distances

    # =========================================================================
    # Reference Tracker: Aggregated learning from stationary room beacons
    # =========================================================================

    def _aggregate_reference_tracker_readings(
        self,
        nowstamp: float,
    ) -> dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]]:
        """
        Aggregate RSSI from reference trackers, grouped by area.

        Multiple trackers in the same room produce exactly ONE aggregated
        entry via per-scanner median. Returns one tuple per area:
        (primary_rssi, primary_scanner_addr, other_readings, scanner_stamps).
        """
        result: dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]] = {}

        # Step 1: Group reference trackers by area_id
        ref_by_area: dict[str, list[Any]] = {}
        for device in self.devices.values():
            if not getattr(device, "is_reference_tracker", False):
                continue
            if device.area_id is None:
                continue
            ref_by_area.setdefault(device.area_id, []).append(device)

        if not ref_by_area:
            return result

        # Step 2: Per area, collect all RSSI readings and compute median
        for area_id, trackers in ref_by_area.items():
            scanner_rssi_lists: dict[str, list[float]] = {}
            scanner_stamps: dict[str, float] = {}

            for tracker in trackers:
                for advert in tracker.adverts.values():
                    if (
                        advert.rssi is not None
                        and advert.stamp is not None
                        and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                        and advert.scanner_address is not None
                    ):
                        scanner_rssi_lists.setdefault(advert.scanner_address, []).append(advert.rssi)
                        scanner_stamps[advert.scanner_address] = max(
                            scanner_stamps.get(advert.scanner_address, 0.0),
                            advert.stamp,
                        )

            if not scanner_rssi_lists:
                continue

            # Step 3: Compute per-scanner median
            scanner_medians: dict[str, float] = {
                addr: statistics.median(rssis) for addr, rssis in scanner_rssi_lists.items()
            }

            # Step 4: Determine primary scanner (strongest median)
            primary_addr = max(scanner_medians, key=lambda k: scanner_medians[k])
            primary_rssi = scanner_medians[primary_addr]
            other_readings = {k: v for k, v in scanner_medians.items() if k != primary_addr}

            result[area_id] = (primary_rssi, primary_addr, other_readings, scanner_stamps)

        return result

    def _update_reference_tracker_learning(self, nowstamp: float) -> None:
        """
        Perform one aggregated auto-learning update per area from reference trackers.

        Called once per coordinator cycle, BEFORE individual device learning.
        N reference trackers in the same room produce exactly ONE learning update.
        """
        aggregated = self._aggregate_reference_tracker_readings(nowstamp)

        # Cache for diagnostics
        self._last_ref_tracker_aggregation = aggregated

        if not aggregated:
            return

        for area_id, (primary_rssi, primary_addr, other_readings, _stamps) in aggregated.items():
            device_key = f"{REFERENCE_TRACKER_DEVICE_PREFIX}{area_id}"

            # Resolve area name for logging/diagnostics
            area_name = self.resolve_area_name(area_id) or area_id

            # Create lightweight proxy that mimics BermudaDevice interface
            proxy = _ReferenceTrackerProxy(
                address=device_key,
                name=f"Reference Tracker ({area_name})",
                area_id=area_id,
            )

            # Call shared learning method with elevated confidence.
            # Filter 3 (Movement State) is bypassed via ref: prefix check (Phase 6).
            self._update_device_correlations(
                device=cast("BermudaDevice", proxy),
                area_id=area_id,
                primary_rssi=primary_rssi,
                primary_scanner_addr=primary_addr,
                other_readings=other_readings,
                nowstamp=nowstamp,
                confidence=REFERENCE_TRACKER_CONFIDENCE,
            )

    # =========================================================================
    # Main entry point - refresh areas for all devices
    # =========================================================================

    def refresh_areas_by_min_distance(self) -> None:
        """Set area for ALL devices based on UKF+RoomProfile or min-distance fallback."""
        nowstamp = monotonic_time_coarse()

        # Phase 0: Update scanner online/offline status before processing devices.
        # This must run ONCE per cycle, before any device iteration, so all devices
        # see a consistent scanner status snapshot.
        self._update_scanner_online_status(nowstamp)

        # Cache offline scanner addresses once per cycle to avoid redundant
        # frozenset creation (was called 4x per device before).
        self._cycle_offline_addrs = self._get_offline_scanner_addrs()

        # Reference Tracker: Aggregated learning BEFORE individual device processing.
        # This ensures N trackers in the same room produce exactly ONE learning update.
        self._update_reference_tracker_learning(nowstamp)

        # Check if we have mature room profiles (scanner-pairs with sufficient samples)
        has_mature_profiles = any(
            profile.mature_pair_count >= MATURE_PROFILE_MIN_PAIRS for profile in self.room_profiles.values()
        )

        for device in self.devices.values():
            self._determine_area_for_device(device, has_mature_profiles=has_mature_profiles)

    def _determine_area_for_device(self, device: BermudaDevice, *, has_mature_profiles: bool) -> None:
        """
        Determine and set the area for a single device.

        This method handles the complete area determination flow:
        1. Check if device needs processing (is tracked and not a scanner)
        2. Handle manual area locks from training UI
        3. Try UKF fingerprint matching (when profiles are mature or device has correlations)
        4. Fall back to min-distance heuristic

        Args:
        ----
            device: The BermudaDevice to determine area for
            has_mature_profiles: Whether the system has mature RoomProfiles globally

        """
        # Skip scanners and devices not being tracked
        if device.is_scanner or not (device.create_sensor or device.create_tracker_done):
            return

        # Check if device is manually locked to an area
        if device.area_locked_id is not None:
            # Device is locked by user selection for training.
            if device.area_locked_scanner_addr is not None:
                locked_advert = None
                for advert in device.adverts.values():
                    if advert.scanner_address == device.area_locked_scanner_addr:
                        locked_advert = advert
                        break

                if locked_advert is None:
                    _LOGGER.info(
                        "Auto-unlocking %s: locked scanner %s no longer has any advert",
                        device.name,
                        device.area_locked_scanner_addr,
                    )
                    device.area_locked_id = None
                    device.area_locked_name = None
                    device.area_locked_scanner_addr = None
                else:
                    # FIX: ACTIVE OVERRIDE - Set the device area to the locked area immediately.
                    device.update_area_and_floor(device.area_locked_id)
                    return
            else:
                # Scannerless room: no specific scanner to track, just force the area.
                device.update_area_and_floor(device.area_locked_id)
                return

        # Primary: UKF with RoomProfile (when profiles are mature)
        # Fallback: Simple min-distance (bootstrap phase)
        device_has_correlations = device.address in self.correlations and len(self.correlations[device.address]) > 0
        if (has_mature_profiles or device_has_correlations) and self._refresh_area_by_ukf(device):
            return
        self._refresh_area_by_min_distance(device)

    # =========================================================================
    # UKF-based area selection
    # =========================================================================

    def _apply_ukf_selection(
        self,
        device: BermudaDevice,
        best_advert: BermudaAdvert,
        best_area_id: str,
        *,
        scanner_less_room: bool,
        match_score: float,
        nowstamp: float,
    ) -> None:
        """Apply the UKF-selected area to the device and update correlations."""
        if scanner_less_room:
            # Override the advert's area with the UKF-matched area.
            # IMPORTANT: Temporarily clear scanner_device so apply_scanner_selection
            # uses our overridden area_id instead of scanner_device.area_id
            # (see bermuda_device.py apply_scanner_selection priority logic)
            #
            # FIX: BUG 8 - Save ALL modified attributes to restore after use
            # Without full restoration, the advert object remains "tainted" with
            # the virtual room's area_id, causing incorrect calibration data.
            saved_scanner_device = best_advert.scanner_device
            saved_area_id = best_advert.area_id
            saved_area_name = best_advert.area_name

            try:
                best_advert.scanner_device = None  # type: ignore[assignment]  # Temp for area override
                best_advert.area_id = best_area_id
                best_advert.area_name = self.resolve_area_name(best_area_id)

                device.apply_scanner_selection(best_advert, nowstamp=nowstamp)
            finally:
                # Restore ALL modified attributes to prevent dirty object state
                best_advert.scanner_device = saved_scanner_device
                best_advert.area_id = saved_area_id
                best_advert.area_name = saved_area_name

            # FIX: BUG 18 - Calculate virtual distance for scannerless rooms
            # Previously (BUG 13 fix) we set area_distance=None, but this shows "Unbekannt"
            # in the UI which confuses users. Instead, calculate a virtual distance from the
            # UKF match score using the same formula as _get_virtual_distances_for_scannerless_rooms.
            # Formula: distance = max_radius * VIRTUAL_DISTANCE_SCALE * (1 - score)²
            max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
            virtual_distance = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - match_score) ** 2)
            device.area_distance = virtual_distance
            device.area_distance_stamp = nowstamp
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "UKF scannerless room %s for %s: score=%.3f → virtual distance=%.2fm",
                    best_area_id,
                    device.name,
                    match_score,
                    virtual_distance,
                )
        else:
            # Apply the selection using the device's standard method
            device.apply_scanner_selection(best_advert, nowstamp=nowstamp)

        # AUTO-LEARNING: Update correlations so fingerprints adapt to environment changes
        if best_advert.rssi is not None and best_area_id is not None:
            # Collect RSSI readings from other visible scanners
            other_readings: dict[str, float] = {}
            for other_adv in device.adverts.values():
                if (
                    other_adv is not best_advert
                    and other_adv.stamp is not None
                    and nowstamp - other_adv.stamp < EVIDENCE_WINDOW_SECONDS
                    and other_adv.rssi is not None
                    and other_adv.scanner_address is not None
                ):
                    other_readings[other_adv.scanner_address] = other_adv.rssi

            # Use shared method to update both device and room profiles
            # Pass UKF match_score as confidence for Feature 3 quality filter
            self._update_device_correlations(
                device=device,
                area_id=best_area_id,
                primary_rssi=best_advert.rssi,
                primary_scanner_addr=best_advert.scanner_address,
                other_readings=other_readings,
                nowstamp=nowstamp,
                confidence=match_score,
            )

    def _refresh_area_by_ukf(self, device: BermudaDevice) -> bool:  # noqa: PLR0911, C901
        """
        Use UKF (Unscented Kalman Filter) for area selection via fingerprint matching.

        This method maintains a per-device UKF that fuses RSSI readings from all visible
        scanners. It then matches the fused state against learned area fingerprints to
        determine the most likely area.

        Returns True if a decision was made (area may or may not have changed),
        False if UKF cannot make a decision (e.g., insufficient scanners or profiles).
        """
        nowstamp = monotonic_time_coarse()

        # Create AreaTests for diagnostic output
        tests = AreaTests()
        tests.device = device.name or device.address
        tests.decision_path = "UKF"

        # Collect RSSI readings from all visible scanners
        rssi_readings: dict[str, float] = {}
        recovering_scanners: list[str] = []
        for advert in device.adverts.values():
            if (
                advert.rssi is not None
                and advert.scanner_address is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            ):
                # Phase 4: Recovery dampening — exclude scanners in grace period
                # When a scanner just came back online, its first readings may be
                # unreliable (stale Kalman state, bursty advertisements).
                # Exclude from UKF matching during the grace period to prevent
                # immediate room-switching based on unreliable data.
                if self._is_scanner_recovering(advert.scanner_address, nowstamp):
                    recovering_scanners.append(advert.scanner_address)
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "UKF recovery dampening: excluding scanner %s for %s (in grace period)",
                            advert.scanner_address,
                            device.name,
                        )
                    continue
                rssi_readings[advert.scanner_address] = advert.rssi

        # Need minimum scanners for UKF to be useful
        # FIX: Bug 3 - Allow single-scanner RETENTION for scannerless rooms
        # In basements/isolated areas, often only 1 distant scanner sees the device.
        # If UKF requires 2 scanners, it bails out and min-distance takes over.
        # min_distance can't detect scannerless rooms → device jumps to scanner's room.
        #
        # Solution: For RETENTION (keeping current area), allow 1 scanner when:
        # 1. Device already has a confirmed area (device.area_id is not None)
        # 2. Device has trained profiles that include that area
        # 3. The single scanner's RSSI is consistent with the trained profile
        device_profiles = self.correlations.get(device.address, {})
        current_area_id = device.area_id

        # Check if this is a retention candidate with trained profiles
        can_retain_with_single_scanner = (
            len(rssi_readings) == 1 and current_area_id is not None and current_area_id in device_profiles
        )

        if len(rssi_readings) < UKF_MIN_SCANNERS and not can_retain_with_single_scanner:
            tests.reason = f"SKIP - insufficient scanners ({len(rssi_readings)} < {UKF_MIN_SCANNERS})"
            # Don't set device.area_tests here - let min_distance handle it
            return False

        # Single-scanner retention: verify RSSI against trained profile
        if can_retain_with_single_scanner and current_area_id is not None:
            scanner_addr = next(iter(rssi_readings))
            current_rssi = rssi_readings[scanner_addr]
            area_profile = device_profiles.get(current_area_id)

            if area_profile is not None:
                # Check if the scanner has an absolute RSSI profile for this area
                abs_profile = area_profile.get_absolute_rssi(scanner_addr)
                if abs_profile is not None:
                    expected_rssi = abs_profile.expected_rssi
                    rssi_variance = abs_profile.variance
                    rssi_delta = abs(current_rssi - expected_rssi)

                    # Allow up to N standard deviations from expected
                    rssi_threshold = UKF_RSSI_SIGMA_MULTIPLIER * math.sqrt(max(rssi_variance, UKF_MIN_RSSI_VARIANCE))

                    if rssi_delta <= rssi_threshold:
                        # RSSI matches profile - retain current area
                        if _LOGGER.isEnabledFor(logging.DEBUG):
                            _LOGGER.debug(
                                "UKF single-scanner retention for %s: "
                                "RSSI %.1f matches profile %.1f ± %.1f for area %s",
                                device.name,
                                current_rssi,
                                expected_rssi,
                                rssi_threshold,
                                current_area_id,
                            )
                        # Apply retention by updating the advert timestamp
                        if device.area_advert is not None:
                            device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)

                        # Populate AreaTests for single-scanner retention
                        tests.ukf_retention_mode = True
                        tests.areas = (current_area_id or "", current_area_id or "")
                        tests.same_area = True
                        tests.profile_has_button = area_profile.has_button_training if area_profile else False
                        tests.profile_sample_count = area_profile.sample_count if area_profile else None
                        tests.reason = (
                            f"WIN - single-scanner retention "
                            f"(RSSI {current_rssi:.0f} ≈ {expected_rssi:.0f}±{rssi_threshold:.0f})"
                        )
                        device.area_tests = tests
                        device.diag_area_switch = tests.sensortext()
                        return True
                    # RSSI doesn't match profile - fall back to min_distance
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "UKF single-scanner retention rejected for %s: RSSI %.1f too far from profile %.1f ± %.1f",
                            device.name,
                            current_rssi,
                            expected_rssi,
                            rssi_threshold,
                        )
                    tests.reason = (
                        f"REJECT - single-scanner RSSI mismatch "
                        f"({current_rssi:.0f} vs {expected_rssi:.0f}±{rssi_threshold:.0f})"
                    )
            # No usable profile - fall back to min_distance
            if tests.reason is None:
                tests.reason = "SKIP - no usable profile for single-scanner retention"
            return False

        # Get or create UKF for this device
        if device.address not in self.device_ukfs:
            self.device_ukfs[device.address] = UnscentedKalmanFilter()

        ukf = self.device_ukfs[device.address]

        # Update UKF with current measurements
        ukf.predict(dt=UPDATE_INTERVAL)
        ukf.update_multi(rssi_readings)

        # Device profiles already fetched above for single-scanner check

        # Need either device profiles or room profiles
        if not device_profiles and not self.room_profiles:
            tests.reason = "SKIP - no device or room profiles"
            return False

        # Match against both device-specific and room-level fingerprints
        # Pass offline scanner addresses so match_fingerprints() can apply coverage penalty
        offline_addrs = self._cycle_offline_addrs
        matches = ukf.match_fingerprints(device_profiles, self.room_profiles, offline_addrs)

        # Populate offline diagnostics into AreaTests
        if offline_addrs:
            tests.offline_scanners_count = len(offline_addrs)
            tests.offline_scanner_addrs = ",".join(sorted(offline_addrs))

        if not matches:
            tests.reason = "SKIP - no fingerprint matches"
            return False

        # Get best match (includes coverage_penalty computed by match_fingerprints)
        best_area_id, _d_squared, match_score, best_coverage_penalty = matches[0]

        # Populate top candidates for diagnostics
        tests.top_candidates = [
            {"area": area_id, "score": round(score, 3), "type": "UKF"} for area_id, _, score, _ in matches[:5]
        ]

        # Coverage penalty diagnostic — read directly from match_fingerprints result
        if best_coverage_penalty > 0.0:
            tests.coverage_penalty_applied = best_coverage_penalty

        # Phase 3 diagnostic: Will auto-learning be blocked for the winning area?
        if offline_addrs and best_area_id in device_profiles:
            _trained_addrs = device_profiles[best_area_id].trained_scanner_addresses
            if _trained_addrs & offline_addrs:
                tests.auto_learning_blocked_offline = True

        # FIX: Sticky Virtual Rooms - Apply stickiness bonus for current area
        # When the device is already in an area (especially a scannerless one),
        # give that area a bonus to prevent marginal flickering.
        #
        # FIX: FEHLER 1 - MUST use device.area_id (confirmed system state), NOT device.area_advert.area_id!
        # device.area_advert is the LAST RECEIVED PACKET, which may be from ANY scanner.
        # For scannerless rooms: device is in "Virtual Room" but scanner in "Hallway" sends packet.
        # device.area_advert would point to "Hallway", giving stickiness bonus to WRONG room!
        # device.area_id is the CONFIRMED current location - the authoritative source of truth.
        # (current_area_id already fetched above for single-scanner check)

        # Check if current area is in the matches and apply stickiness
        effective_match_score = match_score
        current_area_match_score: float | None = None

        if current_area_id is not None:
            for area_id, _d_sq, score, _cp in matches:
                if area_id == current_area_id:
                    current_area_match_score = score
                    break

            # FIX: Sticky Virtual Rooms - If current area has a reasonable score,
            # it needs to be beaten by a significant margin
            if current_area_match_score is not None and best_area_id != current_area_id:
                # Apply stickiness: challenger must beat current by UKF_STICKINESS_BONUS
                stickiness_adjusted_current = current_area_match_score + UKF_STICKINESS_BONUS

                if match_score <= stickiness_adjusted_current:
                    # Current area wins with stickiness bonus
                    best_area_id = current_area_id
                    effective_match_score = current_area_match_score
                    tests.ukf_stickiness_applied = True
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "UKF stickiness for %s: keeping %s (score=%.2f+%.2f bonus) over challenger (score=%.2f)",
                            device.name,
                            current_area_id,
                            current_area_match_score,
                            UKF_STICKINESS_BONUS,
                            match_score,
                        )

        # Check if match score meets minimum threshold (after stickiness adjustment)
        # FIX: FEHLER 3 - Use LOWER threshold for RETENTION (keeping current area)
        # When best_area_id == current_area_id (device would stay in same room), use a much
        # lower threshold (UKF_RETENTION_THRESHOLD) to prevent fallback to min-distance.
        # This keeps scannerless rooms "sticky" even with noisy/weak signals.
        # For SWITCHING to a NEW area, use the normal UKF_MIN_MATCH_SCORE threshold.
        is_retention = best_area_id == current_area_id and current_area_id is not None
        effective_threshold = UKF_RETENTION_THRESHOLD if is_retention else UKF_MIN_MATCH_SCORE

        # Populate UKF diagnostic fields
        tests.ukf_match_score = match_score
        tests.ukf_current_area_score = current_area_match_score
        tests.ukf_retention_mode = is_retention
        tests.ukf_threshold_used = effective_threshold

        if effective_match_score < effective_threshold:
            if is_retention:
                # FIX: FEHLER 3 - For retention case, return True even with low score
                # to prevent fallback to min-distance (which doesn't know scannerless rooms).
                # We still want to refresh the selection to update timestamps etc.
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF retention for %s: score %.2f < %.2f (retention threshold), "
                        "but keeping area %s to avoid min-distance fallback",
                        device.name,
                        effective_match_score,
                        effective_threshold,
                        current_area_id,
                    )
                if device.area_advert is not None:
                    device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)

                # Populate AreaTests for low-score retention
                tests.areas = (current_area_id or "", current_area_id or "")
                tests.same_area = True
                tests.reason = f"WIN - low-score retention ({effective_match_score:.2f} < {effective_threshold:.2f})"
                device.area_tests = tests
                device.diag_area_switch = tests.sensortext()
                return True
            tests.reason = f"REJECT - score too low ({effective_match_score:.2f} < {effective_threshold:.2f})"
            return False

        # Find the advert corresponding to the best area
        best_advert: BermudaAdvert | None = None
        for advert in device.adverts.values():
            if advert.area_id == best_area_id:
                best_advert = advert
                break

        if best_advert is None:
            # No current advert for the matched area - check if we can use any
            # advert with a scanner in that area
            for advert in device.adverts.values():
                if advert.scanner_device is not None:
                    scanner_area = getattr(advert.scanner_device, "area_id", None)
                    if scanner_area == best_area_id:
                        best_advert = advert
                        break

        scanner_less_room = False
        if best_advert is None:
            # FIX: BUG 22 - CRITICAL: Check if the area has a REGISTERED scanner!
            # If the area HAS a scanner but we have no advert from it, the scanner
            # simply doesn't see the device. This is DIFFERENT from a true scannerless room.
            #
            # IMPORTANT (Codex review feedback): We use registration check, NOT activity check.
            # Reason: scanner.last_seen only updates when adverts are received. In quiet rooms
            # with little BLE traffic, an online scanner may not receive any adverts for 30+
            # seconds, making it appear "inactive". Using an activity-based check would cause
            # the original bug to reappear after the timeout expires.
            #
            # Trade-off: If a scanner is genuinely offline (proxy crashed), its room won't be
            # selectable via UKF virtual assignment. However, min-distance fallback will still
            # work using other scanners. This is safer than risking incorrect placements.
            #
            # Decision matrix:
            # - Area has REGISTERED scanner, no advert → REJECT (scanner doesn't see device)
            # - Area truly has no scanner → proceed with scannerless room logic
            if self._area_has_scanner(best_area_id):
                # Area HAS a registered scanner, but that scanner doesn't see this device!
                # This means the device is too far from the scanner to be in this area.
                # DO NOT treat this as a "scannerless room" - reject the UKF decision.
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "BUG 22 FIX: UKF picked %s for %s, but the area's scanner "
                        "doesn't see the device - falling back to min-distance",
                        best_area_id,
                        device.name,
                    )
                tests.reason = f"REJECT - scanner in {self.resolve_area_name(best_area_id)} doesn't see device"
                return False

            # True scannerless room: UKF matched an area with no registered scanner.
            # Use the best available advert (strongest RSSI) and override its area.
            strongest_rssi = RSSI_INVALID_SENTINEL
            for advert in device.adverts.values():
                if (
                    advert.rssi is not None
                    and advert.stamp is not None
                    and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                    and advert.rssi > strongest_rssi
                ):
                    strongest_rssi = advert.rssi
                    best_advert = advert

            if best_advert is None:
                tests.reason = "REJECT - no advert available for scannerless room"
                return False

            scanner_less_room = True
            tests.is_scannerless_room = True

            # BUG 21 FIX: TOPOLOGICAL SANITY CHECK FOR SCANNERLESS ROOMS
            # When UKF picks a scannerless room on floor X, at least ONE scanner on floor X
            # must see the device. If NO scanner on the target floor sees the device,
            # the UKF decision is topologically impossible.
            #
            # This is robust because it doesn't rely on static RSSI thresholds which
            # vary by scanner/tracker hardware. Instead, it checks: "Is there ANY evidence
            # that the device is actually on this floor?"
            #
            # Example scenario this fixes:
            # - Device is in "Lagerraum" (basement, scannerless)
            # - UKF picks "Bad OG" (bathroom, 2 floors up, scannerless) with score 0.83
            # - But NO scanner on the "OG" floor sees the device
            # - Only basement scanners see the device
            # - Topologically impossible → reject UKF decision
            target_area_floor_id = self._resolve_floor_id_for_area(best_area_id)

            if target_area_floor_id is not None:
                # Check if any scanner on the target floor sees the device
                scanner_on_target_floor_sees_device = False

                for advert in device.adverts.values():
                    if (
                        advert.stamp is not None
                        and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                        and advert.scanner_device is not None
                    ):
                        scanner_floor_id = getattr(advert.scanner_device, "floor_id", None)
                        if scanner_floor_id == target_area_floor_id:
                            scanner_on_target_floor_sees_device = True
                            break

                tests.passed_topological_check = scanner_on_target_floor_sees_device

                if not scanner_on_target_floor_sees_device:
                    # UKF picked a scannerless room on a floor where NO scanner sees
                    # the device. This is topologically impossible.
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "BUG 21 FIX: UKF scannerless topological check FAILED for %s: "
                            "UKF picked %s (floor %s), but NO scanner on that floor sees "
                            "the device - falling back to min-distance",
                            device.name,
                            best_area_id,
                            target_area_floor_id,
                        )
                    tests.reason = f"REJECT - topological check (no scanner on floor {target_area_floor_id})"
                    return False

        # Track whether current area is scannerless (for stickiness in future cycles)
        device.ukf_scannerless_area = scanner_less_room

        # RSSI SANITY CHECK:
        # Only reject UKF decision if BOTH conditions are met:
        # 1. The selected room has significantly weaker signal (>15 dB)
        # 2. The UKF match score is borderline (< 0.6)
        #
        # If UKF has high confidence, trust it even with weaker signal - this allows
        # proper handling of scanner-less rooms and blocked/dampened scanners.
        # The fingerprint pattern is more reliable than raw RSSI in these cases.
        if not scanner_less_room and best_advert is not None:
            best_advert_rssi = best_advert.rssi
            strongest_visible_rssi = RSSI_INVALID_SENTINEL

            for advert in device.adverts.values():
                if (
                    advert.rssi is not None
                    and advert.stamp is not None
                    and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                    and advert.rssi > strongest_visible_rssi
                ):
                    strongest_visible_rssi = advert.rssi

            # Only apply sanity check when UKF confidence is low AND signal is much weaker
            rssi_sanity_failed = (
                effective_match_score < UKF_LOW_CONFIDENCE_THRESHOLD
                and best_advert_rssi is not None
                and strongest_visible_rssi > RSSI_INVALID_SENTINEL
                and strongest_visible_rssi - best_advert_rssi > UKF_RSSI_SANITY_MARGIN
            )
            tests.passed_rssi_sanity = not rssi_sanity_failed

            if rssi_sanity_failed:
                # Low confidence UKF picked a room with weak signal - suspicious
                rssi_diff = strongest_visible_rssi - (best_advert_rssi or 0)
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF sanity check failed for %s: UKF picked %s (score=%.2f, RSSI %.1f) but "
                        "strongest signal is %.1f dB stronger - falling back to min-distance",
                        device.name,
                        best_area_id,
                        effective_match_score,
                        best_advert_rssi,
                        rssi_diff,
                    )
                tests.reason = f"REJECT - RSSI sanity (score {effective_match_score:.2f}, diff {rssi_diff:.0f}dB)"
                return False

        # DISTANCE-BASED SANITY CHECK (BUG 14):
        # When a device is VERY close to a scanner, it's almost certainly in that
        # scanner's room. UKF fingerprints can be wrong (bad training), but physical
        # distance doesn't lie. This prevents the bug where UKF picks a room 2 floors
        # away when the device is 1.6m from a scanner.
        #
        # Only reject UKF if:
        # 1. There's a scanner VERY close to the device
        # 2. UKF picked a DIFFERENT area than that scanner's area
        # 3. The scanner has a valid area assigned
        nearest_scanner_distance = DISTANCE_INFINITE_SENTINEL
        nearest_scanner_area_id: str | None = None
        nearest_scanner_floor_id: str | None = None

        for advert in device.adverts.values():
            if (
                advert.rssi_distance is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                and advert.rssi_distance < nearest_scanner_distance
                and advert.scanner_device is not None
            ):
                scanner_area = getattr(advert.scanner_device, "area_id", None)
                if scanner_area is not None:
                    nearest_scanner_distance = advert.rssi_distance
                    nearest_scanner_area_id = scanner_area
                    nearest_scanner_floor_id = getattr(advert.scanner_device, "floor_id", None)

        # Populate proximity info for diagnostics
        if nearest_scanner_distance < DISTANCE_INFINITE_SENTINEL:
            tests.nearest_scanner_distance = nearest_scanner_distance
            tests.nearest_scanner_area = (
                self.resolve_area_name(nearest_scanner_area_id) if nearest_scanner_area_id else None
            )

        if (
            nearest_scanner_distance < UKF_PROXIMITY_THRESHOLD_METERS
            and nearest_scanner_area_id is not None
            and nearest_scanner_area_id != best_area_id
        ):
            # Device is very close to a scanner but UKF picked a different room!
            # Check if this is a cross-floor decision (even more suspicious)
            ukf_floor_id = self._resolve_floor_id_for_area(best_area_id) if not scanner_less_room else None
            is_cross_floor_ukf = (
                nearest_scanner_floor_id is not None
                and ukf_floor_id is not None
                and nearest_scanner_floor_id != ukf_floor_id
            )

            if is_cross_floor_ukf:
                # UKF picked a room on a DIFFERENT floor while device is <2m from a scanner.
                # This is almost certainly wrong - fall back to min-distance.
                tests.passed_proximity_check = False
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF distance sanity check FAILED for %s: Device is %.1fm from scanner "
                        "in %s (floor %s), but UKF picked %s (floor %s) - falling back to min-distance",
                        device.name,
                        nearest_scanner_distance,
                        nearest_scanner_area_id,
                        nearest_scanner_floor_id,
                        best_area_id,
                        ukf_floor_id,
                    )
                tests.reason = f"REJECT - proximity cross-floor ({nearest_scanner_distance:.1f}m)"
                return False

            # Same floor but different room while very close - allow only with very high confidence
            if effective_match_score < UKF_HIGH_CONFIDENCE_OVERRIDE:
                tests.passed_proximity_check = False
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF distance sanity check FAILED for %s: Device is %.1fm from scanner "
                        "in %s, UKF picked %s with score %.2f < %.2f - falling back to min-distance",
                        device.name,
                        nearest_scanner_distance,
                        nearest_scanner_area_id,
                        best_area_id,
                        effective_match_score,
                        UKF_HIGH_CONFIDENCE_OVERRIDE,
                    )
                tests.reason = (
                    f"REJECT - proximity low-conf ({nearest_scanner_distance:.1f}m, score {effective_match_score:.2f})"
                )
                return False

            # Passed proximity check with high confidence
            tests.passed_proximity_check = True
        else:
            # No proximity conflict
            tests.passed_proximity_check = True

        # CROSS-FLOOR STREAK PROTECTION:
        # Prevent rapid flickering between floors by requiring multiple consecutive
        # cycles picking the same target before allowing a cross-floor switch.
        #
        # FIX: FEHLER 1 (continued) - Use device.area_id for current area, NOT device.area_advert!
        # device.area_advert is the last received packet (may be from ANY scanner),
        # device.area_id is the CONFIRMED current location (authoritative source of truth).
        current_device_area_id = device.area_id

        # FIX: Unified Floor Guard - ALWAYS resolve floor_id from AREA, not from scanner_device
        # For scannerless rooms, the scanner_device belongs to a different area (and floor!)
        # entirely. We must ALWAYS look up the floor_id from the area registry directly to
        # ensure cross-floor protection works correctly for scannerless rooms.
        #
        # Bug fixed: Previously we tried scanner_device.floor_id first, which was WRONG for
        # scannerless rooms (e.g., device in "Office" Floor 1 but using scanner from
        # "Bedroom" Floor 2 would incorrectly think current floor was Floor 2).
        current_floor_id = None
        if current_device_area_id is not None:
            # FIX: Resolve floor from device.area_id (authoritative), not from advert's area
            current_floor_id = self._resolve_floor_id_for_area(current_device_area_id)

        # FIX: Unified Floor Guard - Resolve winner floor_id from TARGET AREA, not scanner
        # For scannerless rooms, best_advert.scanner_device is from a different room!
        winner_floor_id = None
        if scanner_less_room:
            # Scannerless room: get floor_id from the TARGET area (best_area_id)
            winner_floor_id = self._resolve_floor_id_for_area(best_area_id)
        elif best_advert is not None and best_advert.scanner_device is not None:
            # Scanner-based room: can use scanner_device's floor_id
            winner_floor_id = getattr(best_advert.scanner_device, "floor_id", None)

        is_cross_floor = (
            current_floor_id is not None and winner_floor_id is not None and current_floor_id != winner_floor_id
        )

        # Populate profile info from best area
        best_profile = device_profiles.get(best_area_id)
        if best_profile is not None:
            tests.profile_has_button = best_profile.has_button_training
            tests.profile_sample_count = best_profile.sample_count
            if best_profile.has_button_training:
                tests.profile_source = "BUTTON_TRAINED"
            elif tests.profile_sample_count and tests.profile_sample_count > 0:
                tests.profile_source = "AUTO_LEARNED"

        # If same area as current, just refresh the selection
        # FIX: FEHLER 1 (continued) - Compare with device.area_id, not current_area_advert.area_id
        if current_device_area_id is not None and best_area_id == current_device_area_id:
            device.reset_pending_state()
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )

            # Populate AreaTests for same-area refresh
            tests.areas = (current_device_area_id, best_area_id)
            tests.same_area = True
            scannerless_indicator = " (scannerless)" if scanner_less_room else ""
            tests.reason = f"WIN - same area refresh{scannerless_indicator} (score {effective_match_score:.2f})"
            device.area_tests = tests
            device.diag_area_switch = tests.sensortext()
            return True

        # If no current area, bootstrap immediately
        # FIX: FEHLER 1 (continued) - Check device.area_id, not current_area_advert
        if current_device_area_id is None:
            device.reset_pending_state()
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )

            # Populate AreaTests for bootstrap
            tests.areas = ("", best_area_id)
            tests.same_area = False
            scannerless_indicator = " (scannerless)" if scanner_less_room else ""
            tests.reason = f"WIN - bootstrap{scannerless_indicator} (score {effective_match_score:.2f})"
            device.area_tests = tests
            device.diag_area_switch = tests.sensortext()
            return True

        # Determine streak target based on floor change
        streak_target = CROSS_FLOOR_STREAK if is_cross_floor else SAME_FLOOR_STREAK

        # BUG 20 FIX: Only count streak if we have NEW advertisement data
        # Similar to BUG 19 fix for training - prevents cached values from being
        # counted multiple times. BLE devices advertise every 1-10s, but coordinator
        # updates every ~1s. Without this check, the same cached RSSI would count
        # as multiple "votes" for a room switch.
        current_stamps = self._collect_current_stamps(device, nowstamp)
        has_new_data = self._has_new_advert_data(current_stamps, device.pending_last_stamps)

        # Update streak counter - only if we have new data
        if device.pending_area_id == best_area_id and device.pending_floor_id == winner_floor_id:
            # Same target as before - only increment if new data
            if has_new_data:
                device.pending_streak += 1
                device.pending_last_stamps = dict(current_stamps)
            # If no new data, keep current streak count (don't increment)
        elif device.pending_area_id is not None and device.pending_area_id != best_area_id:
            # Different target - reset streak to new candidate
            device.pending_area_id = best_area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1
            device.pending_last_stamps = dict(current_stamps)
        else:
            # First pending or same floor different area
            device.pending_area_id = best_area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1
            device.pending_last_stamps = dict(current_stamps)

        # Check if streak meets threshold
        if device.pending_streak >= streak_target:
            device.reset_pending_state()
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )

            # Populate AreaTests for streak-complete switch
            tests.areas = (current_device_area_id or "", best_area_id)
            tests.same_area = False
            floor_indicator = " cross-floor" if is_cross_floor else ""
            scannerless_indicator = " (scannerless)" if scanner_less_room else ""
            tests.reason = (
                f"WIN -{floor_indicator} switch{scannerless_indicator} "
                f"(streak {device.pending_streak}/{streak_target}, score {effective_match_score:.2f})"
            )
            device.area_tests = tests
            device.diag_area_switch = tests.sensortext()
        # Streak not reached - keep current area
        # NOTE: Use device.area_advert here (the actual advert object), not current_device_area_id.
        # apply_scanner_selection needs an advert object. The area_id determination above
        # correctly uses device.area_id, but the actual selection still needs the advert.
        elif device.area_advert is not None:
            device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)

            # Populate AreaTests for streak-pending (keeping current)
            tests.areas = (current_device_area_id or "", best_area_id)
            tests.same_area = current_device_area_id == best_area_id
            floor_indicator = " cross-floor" if is_cross_floor else ""
            tests.reason = (
                f"PENDING -{floor_indicator} streak {device.pending_streak}/{streak_target} "
                f"(score {effective_match_score:.2f})"
            )
            device.area_tests = tests
            device.diag_area_switch = tests.sensortext()

        return True

    # =========================================================================
    # Min-distance area selection
    # =========================================================================

    def _refresh_area_by_min_distance(self, device: BermudaDevice) -> None:  # noqa: C901
        """Very basic Area setting by finding closest proxy to a given device."""
        # The current area_scanner (which might be None) is the one to beat.
        incumbent: BermudaAdvert | None = device.area_advert
        soft_incumbent: BermudaAdvert | None = None

        _max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
        _use_physical_rssi_priority = self.options.get(
            CONF_USE_PHYSICAL_RSSI_PRIORITY, DEFAULT_USE_PHYSICAL_RSSI_PRIORITY
        )
        nowstamp = monotonic_time_coarse()
        evidence_cutoff = nowstamp - EVIDENCE_WINDOW_SECONDS

        tests = AreaTests()
        tests.device = device.name
        tests.decision_path = "MIN_DISTANCE"

        _superchatty = False  # Set to true for very verbose logging about area wins

        # Create analyzer for this update cycle (replaces nested functions)
        analyzer = AdvertAnalyzer(
            device=device,
            nowstamp=nowstamp,
            evidence_cutoff=evidence_cutoff,
            max_radius=_max_radius,
            effective_distance_fn=lambda adv: self.effective_distance(adv, nowstamp),
        )

        has_distance_contender = analyzer.has_distance_contender()

        # FIX: Weak Scanner Override Protection
        _protect_scannerless_area = device.ukf_scannerless_area
        _scannerless_min_dist_override = UKF_WEAK_SCANNER_MIN_DISTANCE

        # FIX: Use has_valid_distance() instead of is_distance_contender() for incumbent.
        # The incumbent should only become "soft" if it has NO distance data (scanner truly
        # not providing data), NOT just because distance > max_radius. RSSI fluctuations
        # can temporarily cause distance to exceed max_radius, which was causing second-by-
        # second flickering when both scanners were actively sending data.
        #
        # The max_radius check is still applied to challengers via is_distance_contender().
        #
        # We also track whether the incumbent has valid distance for the RSSI fallback gate.
        # If incumbent has valid distance (even > max_radius), RSSI fallback should NOT
        # override it - the scanner is actively providing data.
        incumbent_has_valid_distance = analyzer.has_valid_distance(incumbent)
        if not incumbent_has_valid_distance:
            if analyzer.area_candidate(incumbent) and analyzer.within_evidence(incumbent):
                soft_incumbent = incumbent
            incumbent = None

        for challenger in device.adverts.values():
            if not analyzer.within_evidence(challenger):
                continue

            if (incumbent or soft_incumbent) is challenger:
                continue

            if not analyzer.is_distance_contender(challenger):
                continue

            # At this point the challenger is a valid contender...

            current_incumbent = incumbent or soft_incumbent
            incumbent_distance = analyzer.effective_distance(current_incumbent)
            if (
                incumbent_distance is None
                and current_incumbent is not None
                and current_incumbent is soft_incumbent
                and getattr(device, "area_advert", None) is soft_incumbent
                and getattr(device, "area_distance", None) is not None
                and analyzer.within_evidence(current_incumbent)
            ):
                incumbent_distance = device.area_distance
            challenger_scanner = challenger.scanner_device
            if challenger_scanner is None:
                tests.reason = "LOSS - challenger missing scanner metadata"
                continue

            incumbent_scanner = current_incumbent.scanner_device if current_incumbent else None
            inc_floor_level = getattr(incumbent_scanner, "floor_level", None) if incumbent_scanner else None

            # FIX: FEHLER 2 - Unified Floor Guard
            inc_floor_id: str | None = None
            if device.area_id is not None:
                inc_floor_id = self._resolve_floor_id_for_area(device.area_id)
            if inc_floor_id is None and current_incumbent is not None:
                current_inc_area_id = getattr(current_incumbent, "area_id", None)
                if current_inc_area_id is not None:
                    inc_floor_id = self._resolve_floor_id_for_area(current_inc_area_id)
            if inc_floor_id is None and incumbent_scanner is not None:
                inc_floor_id = getattr(incumbent_scanner, "floor_id", None)

            chal_floor_id = getattr(challenger_scanner, "floor_id", None)
            chal_floor_level = getattr(challenger_scanner, "floor_level", None)
            tests.floors = (inc_floor_id, chal_floor_id)
            tests.floor_levels = (inc_floor_level, chal_floor_level)
            cross_floor = inc_floor_id is not None and chal_floor_id is not None and inc_floor_id != chal_floor_id

            # FIX: Weak Scanner Override Protection for Scannerless Rooms
            # Protect scannerless area from being overridden by distant challengers.
            # This protection applies to BOTH cross-floor AND same-floor challengers,
            # since scannerless rooms have no "home" scanner to defend them.
            if _protect_scannerless_area and current_incumbent is not None:
                challenger_dist = analyzer.effective_distance(challenger)
                if challenger_dist is not None and challenger_dist >= _scannerless_min_dist_override:
                    floor_type = "cross-floor" if cross_floor else "same-floor"
                    tests.reason = (
                        f"LOSS - scannerless area protection (challenger at {challenger_dist:.1f}m "
                        f">= {_scannerless_min_dist_override:.1f}m, {floor_type})"
                    )
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Weak scanner override blocked for %s: scannerless area protected, "
                            "challenger %s at %.1fm (min=%.1fm), %s",
                            device.name,
                            challenger.name,
                            challenger_dist,
                            _scannerless_min_dist_override,
                            floor_type,
                        )
                    continue

            if current_incumbent is None:
                incumbent = challenger
                soft_incumbent = None
                if _superchatty:
                    _LOGGER.debug(
                        "%s IS closer to %s: Incumbent is invalid",
                        device.name,
                        challenger.name,
                    )
                continue

            # Handle soft_incumbent case
            # FIX: Use has_valid_distance() for consistency with the incumbent check above.
            # A soft_incumbent should trigger the special protections if it has NO valid
            # distance data, not just because distance > max_radius.
            if current_incumbent is soft_incumbent and not analyzer.has_valid_distance(soft_incumbent):
                # ABSOLUTE PROFILE RESCUE (with offline-awareness)
                if current_incumbent.area_id is not None:
                    current_area_id = current_incumbent.area_id
                    if device.address in self.correlations and current_area_id in self.correlations[device.address]:
                        profile = self.correlations[device.address][current_area_id]
                        all_readings: dict[str, float] = {}
                        for other_adv in device.adverts.values():
                            if (
                                analyzer.within_evidence(other_adv)
                                and other_adv.rssi is not None
                                and other_adv.scanner_address is not None
                            ):
                                all_readings[other_adv.scanner_address] = other_adv.rssi

                        if profile.mature_absolute_count >= MATURE_ABSOLUTE_MIN_COUNT:
                            z_scores = profile.get_absolute_z_scores(all_readings)
                            if len(z_scores) >= MATURE_ABSOLUTE_MIN_COUNT:
                                avg_z = sum(z for _, z in z_scores) / len(z_scores)

                                # Phase 2: Offline-aware z-score threshold
                                # When the area's primary scanner is offline, the soft
                                # incumbent has no distance data BECAUSE of the outage,
                                # not because the device left. Relax the z-score threshold
                                # to make rescue more effective during scanner outages.
                                offline_addrs_rescue = self._cycle_offline_addrs
                                trained_scanners = profile.trained_scanner_addresses
                                offline_trained = trained_scanners & offline_addrs_rescue
                                rescue_z_threshold = ABSOLUTE_Z_SCORE_MAX
                                offline_context = ""
                                if offline_trained:
                                    # Relax threshold proportional to fraction of trained
                                    # scanners that are offline (1.0 = no leniency, 2.0 = max)
                                    offline_fraction = len(offline_trained) / len(trained_scanners)
                                    leniency = 1.0 + offline_fraction
                                    rescue_z_threshold = ABSOLUTE_Z_SCORE_MAX * leniency
                                    offline_context = (
                                        f", offline_scanners={len(offline_trained)}, threshold={rescue_z_threshold:.1f}"
                                    )

                                if avg_z < rescue_z_threshold:
                                    tests.reason = (
                                        f"LOSS - absolute profile match "
                                        f"(z={avg_z:.2f}{offline_context}) "
                                        f"protects current area"
                                    )
                                    if _superchatty:
                                        _LOGGER.debug(
                                            "%s: Absolute profile rescue - secondary readings match "
                                            "%s profile (avg_z=%.2f, scanners=%d, "
                                            "offline=%d, threshold=%.1f)",
                                            device.name,
                                            current_area_id,
                                            avg_z,
                                            len(z_scores),
                                            len(offline_trained),
                                            rescue_z_threshold,
                                        )
                                    continue
                if cross_floor:
                    challenger_hist = challenger.hist_distance_by_interval
                    incumbent_hist = soft_incumbent.hist_distance_by_interval if soft_incumbent else []
                    if len(challenger_hist) < CROSS_FLOOR_MIN_HISTORY:
                        tests.reason = "LOSS - soft incumbent but cross-floor history too short"
                        continue
                    if len(incumbent_hist) >= CROSS_FLOOR_MIN_HISTORY * 2:
                        if len(challenger_hist) < len(incumbent_hist) // 2:
                            tests.reason = "LOSS - soft incumbent has substantial history, challenger needs more"
                            continue
                else:
                    # FIX: Same-floor soft incumbent stabilization
                    soft_inc_distance = device.area_distance if device.area_distance is not None else None
                    soft_inc_was_within_range = soft_inc_distance is not None and soft_inc_distance <= _max_radius

                    if soft_inc_was_within_range and soft_inc_distance is not None:
                        challenger_hist = challenger.hist_distance_by_interval
                        challenger_dist = analyzer.effective_distance(challenger)
                        soft_inc_min_history = CROSS_FLOOR_MIN_HISTORY // SOFT_INC_MIN_HISTORY_DIVISOR
                        soft_inc_min_distance_advantage = SOFT_INC_MIN_DISTANCE_ADVANTAGE

                        has_significant_distance_advantage = (
                            challenger_dist is not None
                            and (soft_inc_distance - challenger_dist) >= soft_inc_min_distance_advantage
                        )
                        has_sufficient_history = len(challenger_hist) >= soft_inc_min_history

                        if not has_significant_distance_advantage and not has_sufficient_history:
                            dist_adv_str = (
                                f"{soft_inc_distance - challenger_dist:.2f}" if challenger_dist is not None else "N/A"
                            )
                            tests.reason = (
                                f"LOSS - soft incumbent same-floor protection "
                                f"(dist adv: {dist_adv_str}m < {soft_inc_min_distance_advantage}m, "
                                f"hist: {len(challenger_hist)} < {soft_inc_min_history})"
                            )
                            if _superchatty:
                                _LOGGER.debug(
                                    "%s: Soft incumbent same-floor protection - %s rejected "
                                    "(distance advantage %.2fm < %.2fm, history %d < %d)",
                                    device.name,
                                    challenger.name,
                                    (soft_inc_distance - challenger_dist) if challenger_dist else 0,
                                    soft_inc_min_distance_advantage,
                                    len(challenger_hist),
                                    soft_inc_min_history,
                                )
                            continue

                # FIX: Challenger must have a valid area_id to replace soft incumbent.
                # Without this check, a scanner without room assignment can "win"
                # and the device switches to UNKNOWN state.
                challenger_area_id = getattr(challenger, "area_id", None)
                if challenger_area_id is None:
                    tests.reason = "LOSS - challenger has no area_id (would cause UNKNOWN state)"
                    if _superchatty:
                        _LOGGER.debug(
                            "%s: Soft incumbent protection - %s rejected (no area_id)",
                            device.name,
                            challenger.name,
                        )
                    continue

                tests.reason = "WIN - soft incumbent failed distance contention"
                incumbent = challenger
                soft_incumbent = None
                continue

            if incumbent_scanner is None:
                tests.reason = "LOSS - incumbent missing scanner metadata"
                continue

            if current_incumbent.area_id is None:
                incumbent = challenger
                soft_incumbent = None
                continue

            if incumbent_distance is None:
                if cross_floor:
                    challenger_hist = challenger.hist_distance_by_interval
                    if len(challenger_hist) < CROSS_FLOOR_MIN_HISTORY:
                        tests.reason = "LOSS - incumbent distance unavailable but cross-floor history too short"
                        continue
                tests.reason = "WIN - incumbent distance unavailable"
                incumbent = challenger
                soft_incumbent = None
                continue

            # Distance comparison checks
            challenger_distance = analyzer.effective_distance(challenger)
            if challenger_distance is None:
                continue

            # Physical RSSI Priority
            challenger_rssi_advantage = 0.0
            if _use_physical_rssi_priority:
                challenger_median_rssi = challenger.median_rssi()
                incumbent_median_rssi = current_incumbent.median_rssi()
                if challenger_median_rssi is not None and incumbent_median_rssi is not None:
                    challenger_rssi_advantage = challenger_median_rssi - incumbent_median_rssi

            passed_via_rssi_override = False
            if incumbent_distance < challenger_distance:
                if _use_physical_rssi_priority and challenger_rssi_advantage > RSSI_CONSISTENCY_MARGIN_DB:
                    passed_via_rssi_override = True
                    if _superchatty:
                        _LOGGER.debug(
                            "RSSI priority override: %s allowed despite further distance "
                            "(RSSI advantage %.1f dB > %.1f dB margin)",
                            challenger.name,
                            challenger_rssi_advantage,
                            RSSI_CONSISTENCY_MARGIN_DB,
                        )
                else:
                    continue

            # STABILITY CHECK (Variance-Based)
            # Uses Kalman filter variance propagation for scientifically correct thresholds.
            # Distance improvement must exceed combined uncertainty * sigma factor.
            if not passed_via_rssi_override:
                distance_improvement = incumbent_distance - challenger_distance
                if distance_improvement > 0:
                    # Get distance variances from both adverts
                    incumbent_variance = current_incumbent.get_distance_variance(nowstamp)
                    challenger_variance = challenger.get_distance_variance(nowstamp)

                    # Combined standard deviation for stability check
                    # KEY FIX: When incumbent is STALE (no recent data), it should be
                    # EASIER to beat, not harder. A stale incumbent means we're uncertain
                    # where it is, so we shouldn't inflate the threshold with its variance.
                    #
                    # We use time-based staleness based on the advert's adaptive_timeout,
                    # which is calculated from observed advertisement intervals (60-360s).
                    # This respects the device's actual behavior rather than using an
                    # arbitrary fixed threshold.
                    incumbent_last_update = current_incumbent.rssi_kalman.last_update_time
                    incumbent_adaptive_timeout = getattr(current_incumbent, "adaptive_timeout", AREA_MAX_AD_AGE_DEFAULT)
                    incumbent_is_stale = (
                        incumbent_last_update is None or (nowstamp - incumbent_last_update) > incumbent_adaptive_timeout
                    )

                    if incumbent_is_stale:
                        # Stale incumbent: exclude its variance - high uncertainty = less protection
                        combined_std = math.sqrt(challenger_variance)
                    else:
                        # Fresh incumbent: use both variances as originally designed
                        combined_std = math.sqrt(incumbent_variance + challenger_variance)

                    # Movement-aware sigma factor
                    movement_state = device.get_movement_state(stamp_now=nowstamp)
                    if movement_state == MOVEMENT_STATE_MOVING:
                        sigma_factor = STABILITY_SIGMA_MOVING  # 2.0 sigma
                    elif movement_state == MOVEMENT_STATE_SETTLING:
                        sigma_factor = STABILITY_SIGMA_SETTLING  # 2.0 sigma
                    else:  # STATIONARY
                        sigma_factor = STABILITY_SIGMA_STATIONARY  # 3.0 sigma

                    # Threshold: improvement must exceed sigma_factor * combined_std
                    significance_threshold = sigma_factor * combined_std

                    # Fallback to legacy meters threshold if variance-based is too small
                    # This handles edge cases where both variances are very small
                    if movement_state in (MOVEMENT_STATE_MOVING, MOVEMENT_STATE_SETTLING):
                        min_threshold = INCUMBENT_MARGIN_METERS
                    else:
                        min_threshold = MARGIN_STATIONARY_METERS
                    significance_threshold = max(significance_threshold, min_threshold)

                    meets_stability_margin = distance_improvement >= significance_threshold
                    if not meets_stability_margin:
                        if _superchatty:
                            _LOGGER.debug(
                                "Stability margin (%s): %s rejected "
                                "(%.2fm improvement < %.2fm threshold = %.1f sigma * %.2fm std)",
                                movement_state,
                                challenger.name,
                                distance_improvement,
                                significance_threshold,
                                sigma_factor,
                                combined_std,
                            )
                        continue

            # RSSI consistency check
            if _use_physical_rssi_priority and challenger_rssi_advantage < -RSSI_CONSISTENCY_MARGIN_DB:
                if _superchatty:
                    _LOGGER.debug(
                        "RSSI consistency check: %s rejected (RSSI disadvantage %.1f dB > %.1f dB margin)",
                        challenger.name,
                        -challenger_rssi_advantage,
                        RSSI_CONSISTENCY_MARGIN_DB,
                    )
                continue

            tests.reason = None
            tests.same_area = current_incumbent.area_id == challenger.area_id
            tests.areas = (current_incumbent.area_name or "", challenger.area_name or "")
            tests.scannername = (current_incumbent.name, challenger.name)
            tests.distance = (incumbent_distance, challenger_distance)

            tests.last_ad_age = (
                nowstamp - incumbent_scanner.last_seen,
                nowstamp - challenger_scanner.last_seen,
            )

            tests.this_ad_age = (
                nowstamp - current_incumbent.stamp,
                nowstamp - challenger.stamp,
            )

            _pda = challenger_distance
            _pdb = incumbent_distance
            tests.pcnt_diff = abs(_pda - _pdb) / ((_pda + _pdb) / 2)
            abs_diff = abs(_pda - _pdb)
            avg_dist = (_pda + _pdb) / 2
            cross_floor_margin = CROSS_FLOOR_MARGIN_BASE
            cross_floor_escape = CROSS_FLOOR_ESCAPE_BASE
            history_window = HISTORY_WINDOW
            cross_floor_min_history = CROSS_FLOOR_MIN_HISTORY

            # Same-Floor-Confirmation
            if cross_floor and inc_floor_id is not None:
                incumbent_floor_witnesses = 0
                challenger_floor_witnesses = 0
                challenger_floor_distances: list[float] = []
                witness_floor_levels: set[int] = set()
                for witness_adv in device.adverts.values():
                    if not analyzer.is_distance_contender(witness_adv):
                        continue
                    witness_scanner = witness_adv.scanner_device
                    if witness_scanner is None:
                        continue
                    witness_floor = getattr(witness_scanner, "floor_id", None)
                    witness_level = getattr(witness_scanner, "floor_level", None)
                    witness_dist = analyzer.effective_distance(witness_adv)
                    if witness_floor == inc_floor_id:
                        incumbent_floor_witnesses += 1
                    if witness_floor == chal_floor_id:
                        challenger_floor_witnesses += 1
                        if witness_dist is not None:
                            challenger_floor_distances.append(witness_dist)
                    if isinstance(witness_level, int):
                        witness_floor_levels.add(witness_level)

                if incumbent_floor_witnesses >= MATURE_PROFILE_MIN_PAIRS:
                    extra_margin = FLOOR_WITNESS_MARGIN_INCREMENT * (incumbent_floor_witnesses - 1)
                    cross_floor_margin = min(FLOOR_MARGIN_CAP_60, cross_floor_margin + extra_margin)
                    cross_floor_escape = min(FLOOR_ESCAPE_CAP_80, cross_floor_escape + extra_margin)

                if challenger_floor_witnesses > incumbent_floor_witnesses:
                    witness_imbalance = challenger_floor_witnesses - incumbent_floor_witnesses
                    imbalance_margin = FLOOR_IMBALANCE_MARGIN * witness_imbalance
                    cross_floor_margin = min(FLOOR_MARGIN_CAP_70, cross_floor_margin + imbalance_margin)
                    cross_floor_escape = min(FLOOR_ESCAPE_CAP_85, cross_floor_escape + imbalance_margin)

                near_field_threshold = NEAR_FIELD_THRESHOLD
                if incumbent_distance <= near_field_threshold and challenger_floor_distances:
                    min_challenger_dist = min(challenger_floor_distances)
                    if min_challenger_dist > incumbent_distance:
                        distance_ratio = min_challenger_dist / incumbent_distance
                        if distance_ratio >= FLOOR_DISTANCE_RATIO_THRESHOLD:
                            ratio_margin = FLOOR_RATIO_MARGIN * (distance_ratio - 1.0)
                            cross_floor_margin = min(FLOOR_MARGIN_CAP_80, cross_floor_margin + ratio_margin)
                            cross_floor_escape = min(FLOOR_ESCAPE_CAP_95, cross_floor_escape + ratio_margin)

                if isinstance(inc_floor_level, int) and len(witness_floor_levels) >= MATURE_PROFILE_MIN_PAIRS:
                    levels_below = [lvl for lvl in witness_floor_levels if lvl < inc_floor_level]
                    levels_above = [lvl for lvl in witness_floor_levels if lvl > inc_floor_level]
                    is_sandwiched = bool(levels_below) and bool(levels_above)

                    if is_sandwiched:
                        sandwich_floors = len(levels_below) + len(levels_above)
                        sandwich_margin = FLOOR_SANDWICH_MARGIN_BASE + FLOOR_SANDWICH_MARGIN_INCREMENT * (
                            sandwich_floors - 2
                        )
                        cross_floor_margin = min(FLOOR_MARGIN_CAP_75, cross_floor_margin + sandwich_margin)
                        cross_floor_escape = min(FLOOR_ESCAPE_CAP_90, cross_floor_escape + sandwich_margin)

                    if isinstance(chal_floor_level, int):
                        floor_distance = abs(chal_floor_level - inc_floor_level)
                        if floor_distance >= 2:
                            skip_margin = FLOOR_SKIP_MARGIN * (floor_distance - 1)
                            cross_floor_margin = min(FLOOR_MARGIN_CAP_80, cross_floor_margin + skip_margin)
                            cross_floor_escape = min(FLOOR_ESCAPE_CAP_95, cross_floor_escape + skip_margin)

            # Same area freshness check
            if (
                tests.same_area
                and (tests.this_ad_age[0] > tests.this_ad_age[1] + 1)
                and tests.distance[0] >= tests.distance[1]
            ):
                tests.reason = "WIN awarded for same area, newer, closer advert"
                incumbent = challenger
                continue

            # Hysteresis checks
            min_history = SAME_FLOOR_MIN_HISTORY
            pdiff_outright = PDIFF_OUTRIGHT
            pdiff_historical = PDIFF_HISTORICAL
            incumbent_hist_all = current_incumbent.hist_distance_by_interval
            challenger_hist_all = challenger.hist_distance_by_interval
            if cross_floor:
                if (
                    len(challenger_hist_all) < cross_floor_min_history
                    or len(incumbent_hist_all) < cross_floor_min_history
                ):
                    tests.reason = "LOSS - cross-floor history too short"
                    continue
            incumbent_history: list[float] = incumbent_hist_all[:history_window]
            challenger_history: list[float] = challenger_hist_all[:history_window]
            if len(challenger.hist_distance_by_interval) > min_history:
                if incumbent_history and challenger_history:
                    tests.hist_min_max = (
                        min(incumbent_history),
                        max(challenger_history),
                    )
                    hist_margin = cross_floor_margin if cross_floor else pdiff_historical
                    if tests.hist_min_max[1] < tests.hist_min_max[0] and tests.pcnt_diff > hist_margin:
                        tests.reason = "WIN on historical min/max"
                        incumbent = challenger
                        continue

            near_field_cutoff = NEAR_FIELD_CUTOFF
            abs_win_meters = NEAR_FIELD_ABS_WIN_METERS
            near_field_win = avg_dist <= near_field_cutoff and abs_diff >= abs_win_meters
            significant_improvement = tests.pcnt_diff >= pdiff_outright

            if cross_floor:
                challenger_history_ready = len(challenger_history) >= history_window
                incumbent_history_ready = len(incumbent_history) >= history_window
                sustained_cross_floor = (
                    challenger_history_ready
                    and incumbent_history_ready
                    and tests.hist_min_max != (0, 0)
                    and tests.hist_min_max[1] < tests.hist_min_max[0]
                    and tests.pcnt_diff > cross_floor_margin
                )
                min_cross_floor_history = max(history_window, cross_floor_min_history // 2)
                has_minimum_history = (
                    len(challenger_history) >= min_cross_floor_history
                    and len(incumbent_history) >= min_cross_floor_history
                )
                cross_floor_escape_strict = max(cross_floor_escape, 1.0)
                escape_with_history = tests.pcnt_diff >= cross_floor_escape_strict and has_minimum_history
                if not (sustained_cross_floor or escape_with_history):
                    tests.reason = "LOSS - cross-floor evidence insufficient"
                    continue

            # RSSI-based wins
            rssi_tiebreak_win = False
            rssi_advantage_win = False
            if _use_physical_rssi_priority:
                challenger_median = challenger.median_rssi()
                incumbent_median = current_incumbent.median_rssi()
                if challenger_median is not None and incumbent_median is not None:
                    rssi_advantage = challenger_median - incumbent_median
                    if abs_diff < DISTANCE_TIE_THRESHOLD and rssi_advantage > 0:
                        rssi_tiebreak_win = True
                    if rssi_advantage > RSSI_CONSISTENCY_MARGIN_DB:
                        rssi_advantage_win = True

            if (
                len(challenger_hist_all) < SAME_FLOOR_MIN_HISTORY
                and not near_field_win
                and not significant_improvement
                and not rssi_tiebreak_win
                and not rssi_advantage_win
            ):
                tests.reason = "LOSS - same-floor history too short"
                continue

            if tests.pcnt_diff < pdiff_outright:
                if not near_field_win:
                    if rssi_advantage_win:
                        tests.reason = "WIN on RSSI advantage (stronger physical signal)"
                        incumbent = challenger
                        continue
                    if rssi_tiebreak_win:
                        tests.reason = "WIN on RSSI tie-break (equal distance)"
                        incumbent = challenger
                        continue
                    tests.reason = "LOSS - failed on percentage_difference"
                    continue
                tests.reason = "WIN on near-field absolute improvement"
                incumbent = challenger
                continue

            tests.reason = "WIN by not losing!"
            incumbent = challenger
            soft_incumbent = None

        if _superchatty and tests.reason is not None:
            _LOGGER.debug(
                "***************\n**************** %s *******************\n%s",
                tests.reason,
                tests,
            )

        rssi_fallback_margin = RSSI_FALLBACK_MARGIN
        rssi_fallback_cross_floor_margin = RSSI_FALLBACK_CROSS_FLOOR_MARGIN
        winner = incumbent or soft_incumbent

        # Virtual Distance for Scannerless Rooms
        virtual_winner_area_id: str | None = None
        virtual_winner_distance: float | None = None

        rssi_readings_for_virtual: dict[str, float] = {}
        for adv in device.adverts.values():
            if analyzer.within_evidence(adv) and adv.rssi is not None and adv.scanner_address is not None:
                rssi_readings_for_virtual[adv.scanner_address] = adv.rssi

        if rssi_readings_for_virtual:
            virtual_distances = self._get_virtual_distances_for_scannerless_rooms(device, rssi_readings_for_virtual)

            if virtual_distances:
                best_virtual_area = min(
                    virtual_distances.keys(),
                    key=lambda area: virtual_distances[area],
                )
                best_virtual_dist = virtual_distances[best_virtual_area]

                winner_distance = analyzer.effective_distance(winner) if winner else None

                if winner is None or winner_distance is None:
                    virtual_winner_area_id = best_virtual_area
                    virtual_winner_distance = best_virtual_dist
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Virtual distance winner for %s: %s at %.2fm (no physical winner)",
                            device.name,
                            best_virtual_area,
                            best_virtual_dist,
                        )
                elif best_virtual_dist < winner_distance:
                    virtual_winner_area_id = best_virtual_area
                    virtual_winner_distance = best_virtual_dist
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Virtual distance winner for %s: %s at %.2fm beats physical %.2fm",
                            device.name,
                            best_virtual_area,
                            best_virtual_dist,
                            winner_distance,
                        )

        # If a virtual room won, apply it directly and return
        if virtual_winner_area_id is not None:
            device.update_area_and_floor(virtual_winner_area_id)
            device.area_distance = virtual_winner_distance
            device.area_distance_stamp = nowstamp
            device.ukf_scannerless_area = True
            device.reset_pending_state()
            tests.reason = f"WIN via virtual distance ({virtual_winner_distance:.2f}m) for scannerless room"
            device.diag_area_switch = tests.sensortext()
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Applied virtual distance winner for %s: area=%s distance=%.2fm",
                    device.name,
                    virtual_winner_area_id,
                    virtual_winner_distance or 0,
                )
            return

        # FIX: RSSI fallback should only run when we have NO distance information at all.
        # If the incumbent has valid distance (even > max_radius), don't use RSSI fallback
        # UNLESS the incumbent itself is > max_radius and no distance contender exists.
        # This prevents flickering when a scanner is actively providing data, while still
        # allowing selection to clear when nothing is in range.
        #
        # The key insight: we want to prevent RSSI fallback from picking a DIFFERENT area
        # when the incumbent has valid distance data, but we still want to clear the
        # selection when nothing is within max_radius.
        incumbent_outside_radius = (
            incumbent is not None
            and analyzer.effective_distance(incumbent) is not None
            and (analyzer.effective_distance(incumbent) or 0) > _max_radius
        )
        skip_rssi_fallback = incumbent_has_valid_distance and not incumbent_outside_radius
        if not has_distance_contender and not skip_rssi_fallback:
            fallback_candidates: list[BermudaAdvert] = []
            for adv in device.adverts.values():
                if not analyzer.area_candidate(adv) or not analyzer.within_evidence(adv):
                    continue
                adv_effective = analyzer.effective_distance(adv)
                if adv_effective is None or adv_effective <= _max_radius:
                    fallback_candidates.append(adv)
            if fallback_candidates:
                best_by_rssi = max(
                    fallback_candidates,
                    key=lambda adv: (
                        adv.rssi if adv.rssi is not None else float("-inf"),
                        adv.stamp if adv.stamp is not None else 0,
                    ),
                )
                incumbent_candidate = (
                    device.area_advert
                    if analyzer.area_candidate(device.area_advert) and analyzer.within_evidence(device.area_advert)
                    else None
                )
                best_rssi = best_by_rssi.rssi
                incumbent_rssi = incumbent_candidate.rssi if incumbent_candidate is not None else None

                rssi_is_cross_floor = False
                if incumbent_candidate is not None and best_by_rssi is not incumbent_candidate:
                    inc_floor = analyzer.get_floor_id(incumbent_candidate)
                    best_floor = analyzer.get_floor_id(best_by_rssi)
                    rssi_is_cross_floor = inc_floor is not None and best_floor is not None and inc_floor != best_floor

                effective_rssi_margin = (
                    rssi_fallback_cross_floor_margin if rssi_is_cross_floor else rssi_fallback_margin
                )

                if incumbent_candidate is None:
                    winner = best_by_rssi
                    tests.reason = "WIN via RSSI fallback (no incumbent within evidence)"
                elif best_by_rssi is incumbent_candidate:
                    winner = best_by_rssi
                    tests.reason = "WIN via RSSI fallback (no distance contenders)"
                elif best_rssi is not None and (
                    incumbent_rssi is None or best_rssi >= incumbent_rssi + effective_rssi_margin
                ):
                    if rssi_is_cross_floor:
                        winner = incumbent_candidate
                        tests.reason = "HOLD via RSSI cross-floor protection (needs streak confirmation)"
                    else:
                        winner = best_by_rssi
                        tests.reason = "WIN via RSSI fallback margin"
                else:
                    winner = incumbent_candidate
                    tests.reason = "HOLD via RSSI fallback hysteresis"

                # Populate diagnostic fields
                winner_name = ""
                winner_area = ""
                winner_distance_val = 0.0
                if winner is not None:
                    winner_name = getattr(winner, "name", "") or ""
                    winner_area = getattr(winner, "area_name", "") or ""
                    winner_distance_val = analyzer.effective_distance(winner) or 0.0
                incumbent_name = ""
                incumbent_area = ""
                incumbent_dist = 0.0
                if incumbent_candidate is not None and incumbent_candidate is not winner:
                    incumbent_name = getattr(incumbent_candidate, "name", "") or ""
                    incumbent_area = getattr(incumbent_candidate, "area_name", "") or ""
                    incumbent_dist = analyzer.effective_distance(incumbent_candidate) or 0.0
                tests.scannername = (incumbent_name, winner_name)
                tests.areas = (incumbent_area, winner_area)
                tests.distance = (incumbent_dist, winner_distance_val)
            else:
                winner = None

        if device.area_advert != winner and tests.reason is not None:
            device.diag_area_switch = tests.sensortext()

        # Apply the newly-found closest scanner
        def _apply_selection(advert: BermudaAdvert | None) -> None:
            device.apply_scanner_selection(advert, nowstamp=nowstamp)

            if advert is not None and advert.area_id is not None:
                visible_scanners = analyzer.get_visible_scanner_addresses()
                known_scanners = analyzer.get_all_known_scanners_for_area(advert.area_id)
                all_candidate_scanners = known_scanners | visible_scanners
                device.update_co_visibility(advert.area_id, visible_scanners, all_candidate_scanners)

                if advert.rssi is not None:
                    other_readings: dict[str, float] = {}
                    for other_adv in device.adverts.values():
                        if (
                            other_adv is not advert
                            and analyzer.within_evidence(other_adv)
                            and other_adv.rssi is not None
                            and other_adv.scanner_address is not None
                        ):
                            other_readings[other_adv.scanner_address] = other_adv.rssi

                    # FIX 1: Compute distance-margin confidence for min-distance
                    # This prevents auto-learning from uncertain/close decisions.
                    # Find the runner-up distance (best distance from a DIFFERENT area).
                    winner_distance = analyzer.effective_distance(advert)
                    runner_up_distance: float | None = None
                    for other_adv in device.adverts.values():
                        if (
                            other_adv is not advert
                            and analyzer.within_evidence(other_adv)
                            and analyzer.has_area(other_adv)
                            and other_adv.area_id != advert.area_id
                        ):
                            other_dist = analyzer.effective_distance(other_adv)
                            if other_dist is not None and (
                                runner_up_distance is None or other_dist < runner_up_distance
                            ):
                                runner_up_distance = other_dist

                    mindist_confidence: float | None = None
                    if winner_distance is not None and runner_up_distance is not None and runner_up_distance > 0:
                        # Confidence = how much closer the winner is relative to runner-up.
                        # 0.0 = equal distances, 1.0 = runner-up infinitely far.
                        # E.g. 2m vs 5m → (5-2)/5 = 0.6 (confident)
                        # E.g. 3.2m vs 3.5m → (3.5-3.2)/3.5 = 0.09 (uncertain)
                        margin_ratio = (runner_up_distance - winner_distance) / runner_up_distance
                        mindist_confidence = max(0.0, min(1.0, margin_ratio))

                    # Use shared method to update both device and room profiles
                    self._update_device_correlations(
                        device=device,
                        area_id=advert.area_id,
                        primary_rssi=advert.rssi,
                        primary_scanner_addr=advert.scanner_address,
                        other_readings=other_readings,
                        nowstamp=nowstamp,
                        confidence=mindist_confidence,
                    )

        if winner is None:
            candidates: list[tuple[float, BermudaAdvert]] = []
            for adv in device.adverts.values():
                if not (analyzer.has_area(adv) and analyzer.within_evidence(adv)):
                    continue
                adv_effective = analyzer.effective_distance(adv)
                if adv_effective is None or adv_effective > _max_radius:
                    continue
                candidates.append((adv_effective, adv))
            if candidates:
                if _use_physical_rssi_priority:

                    def _rssi_sort_key(item: tuple[float, BermudaAdvert]) -> tuple[float, float]:
                        rssi = item[1].median_rssi()
                        return (item[0], -(rssi if rssi is not None else float("-inf")))

                    winner = min(candidates, key=_rssi_sort_key)[1]
                else:
                    winner = min(
                        candidates,
                        key=lambda item: (item[0], item[1].stamp if item[1].stamp is not None else 0),
                    )[1]
                tests.reason = "WIN via rescue candidate"
            if winner is not None:
                _apply_selection(winner)
                return

            if _LOGGER.isEnabledFor(logging.DEBUG):
                fresh_adverts = [adv for adv in device.adverts.values() if analyzer.within_evidence(adv)]
                fresh_with_area = [adv for adv in fresh_adverts if analyzer.has_area(adv)]
                with_effective = [adv for adv in fresh_with_area if analyzer.effective_distance(adv) is not None]
                top_candidates = sorted(
                    fresh_with_area,
                    key=lambda adv: (
                        adv.rssi if adv.rssi is not None else float("-inf"),
                        adv.stamp if adv.stamp is not None else 0,
                    ),
                    reverse=True,
                )[:3]
                top_summary = [
                    f"(age={nowstamp - adv.stamp:.1f}s area={adv.area_id} rssi={adv.rssi} "
                    f"rssi_dist={adv.rssi_distance} hist_len={len(getattr(adv, 'hist_distance_by_interval', []))})"
                    for adv in top_candidates
                ]
                last_log_age = nowstamp - getattr(device, "last_no_winner_log", 0)
                if last_log_age > AREA_MAX_AD_AGE_DEFAULT:
                    device.last_no_winner_log = nowstamp
                    _LOGGER.debug(
                        "Area selection cleared for %s: adverts=%d fresh=%d fresh_with_area=%d "
                        "with_effective=%d max_radius=%.2f top=%s",
                        device.name,
                        len(device.adverts),
                        len(fresh_adverts),
                        len(fresh_with_area),
                        len(with_effective),
                        _max_radius,
                        top_summary,
                    )
            device.reset_pending_state()
            _apply_selection(None)
            return

        if device.area_advert is winner:
            device.reset_pending_state()
            _apply_selection(winner)
            return

        cross_floor_final = analyzer.is_cross_floor(device.area_advert, winner)
        streak_target = CROSS_FLOOR_STREAK if cross_floor_final else SAME_FLOOR_STREAK

        # Calculate confidence scores
        winner_confidence = 1.0
        incumbent_confidence = 1.0
        winner_corr_confidence = 1.0
        incumbent_corr_confidence = 1.0
        low_confidence_winner = False

        if winner is not None and winner.area_id is not None:
            visible_scanners = analyzer.get_visible_scanner_addresses()
            winner_confidence = device.get_co_visibility_confidence(winner.area_id, visible_scanners)

            current_readings: dict[str, float] = {
                adv.scanner_address: adv.rssi
                for adv in device.adverts.values()
                if analyzer.within_evidence(adv) and adv.rssi is not None and adv.scanner_address is not None
            }
            winner_corr_confidence = self._get_correlation_confidence(
                device.address, winner.area_id, winner.rssi, current_readings
            )

            if device.area_advert is not None and device.area_advert.area_id is not None:
                incumbent_confidence = device.get_co_visibility_confidence(device.area_advert.area_id, visible_scanners)
                incumbent_corr_confidence = self._get_correlation_confidence(
                    device.address, device.area_advert.area_id, device.area_advert.rssi, current_readings
                )

            if winner.area_id != (device.area_advert.area_id if device.area_advert else None):
                if (
                    winner_confidence < CONFIDENCE_WINNER_MIN
                    and incumbent_confidence > winner_confidence + CONFIDENCE_WINNER_MARGIN
                ):
                    streak_target = max(streak_target, streak_target * 2)
                    low_confidence_winner = True

                if (
                    winner_corr_confidence < CORR_CONFIDENCE_WINNER_MIN
                    and incumbent_corr_confidence > winner_corr_confidence + CORR_CONFIDENCE_WINNER_MARGIN
                ):
                    streak_target = max(streak_target, streak_target * 2)
                    low_confidence_winner = True

        if device.area_advert is None and winner is not None:
            device.reset_pending_state()
            _apply_selection(winner)
            return

        # Bootstrap checks
        area_advert_stale = False
        significant_improvement_same_floor = False
        significant_rssi_advantage = False
        if device.area_advert is not None:
            max_age = getattr(device.area_advert, "adaptive_timeout", None) or AREA_MAX_AD_AGE_DEFAULT
            max_age = min(max_age, AREA_MAX_AD_AGE_LIMIT)
            area_advert_stale = device.area_advert.stamp < nowstamp - max_age
            if winner is not None:
                streak_winner_dist = analyzer.effective_distance(winner)
                streak_incumbent_dist = analyzer.effective_distance(device.area_advert)
                is_cross_floor_check = analyzer.is_cross_floor(device.area_advert, winner)
                if (
                    streak_winner_dist is not None
                    and streak_incumbent_dist is not None
                    and streak_winner_dist < streak_incumbent_dist
                    and not is_cross_floor_check
                ):
                    streak_avg = (streak_winner_dist + streak_incumbent_dist) / 2
                    streak_pcnt = abs(streak_winner_dist - streak_incumbent_dist) / streak_avg if streak_avg > 0 else 0
                    significant_improvement_same_floor = streak_pcnt >= MINDIST_SIGNIFICANT_IMPROVEMENT

                if _use_physical_rssi_priority and not is_cross_floor_check:
                    winner_rssi = winner.median_rssi() if hasattr(winner, "median_rssi") else None
                    incumbent_rssi_val = (
                        device.area_advert.median_rssi() if hasattr(device.area_advert, "median_rssi") else None
                    )
                    if winner_rssi is not None and incumbent_rssi_val is not None:
                        rssi_advantage_val = winner_rssi - incumbent_rssi_val
                        if rssi_advantage_val > RSSI_CONSISTENCY_MARGIN_DB:
                            significant_rssi_advantage = True

        is_cross_floor_switch = analyzer.is_cross_floor(device.area_advert, winner)
        incumbent_truly_invalid = not analyzer.is_distance_contender(device.area_advert) or area_advert_stale
        same_floor_fast_track = not is_cross_floor_switch and (
            significant_improvement_same_floor or significant_rssi_advantage
        )

        incumbent_completely_offline = (
            device.area_advert is None
            or device.area_advert.stamp is None
            or device.area_advert.stamp < nowstamp - AREA_MAX_AD_AGE_LIMIT
        )

        allow_immediate_switch = winner is not None and (
            (is_cross_floor_switch and incumbent_completely_offline)
            or (not is_cross_floor_switch and (incumbent_truly_invalid or same_floor_fast_track))
        )

        if allow_immediate_switch:
            device.reset_pending_state()
            _apply_selection(winner)
            return

        # Streak logic
        winner_floor_id = getattr(winner.scanner_device, "floor_id", None)

        # BUG 20 FIX: Only count streak if we have NEW advertisement data
        current_stamps = self._collect_current_stamps(device, nowstamp)
        has_new_data = self._has_new_advert_data(current_stamps, device.pending_last_stamps)

        if device.pending_area_id == winner.area_id and device.pending_floor_id == winner_floor_id:
            if (low_confidence_winner and winner_confidence < STREAK_LOW_CONFIDENCE_THRESHOLD) or not has_new_data:
                pass
            else:
                device.pending_streak += 1
                device.pending_last_stamps = dict(current_stamps)
        elif device.pending_area_id is not None and device.pending_area_id != winner.area_id:
            pending_advert = next(
                (adv for adv in device.adverts.values() if adv.area_id == device.pending_area_id),
                None,
            )
            pending_dist = analyzer.effective_distance(pending_advert) if pending_advert else None
            winner_dist = analyzer.effective_distance(winner)

            if pending_dist is not None and winner_dist is not None:
                improvement = (pending_dist - winner_dist) / pending_dist if pending_dist > 0 else 0
                if improvement > MINDIST_PENDING_IMPROVEMENT:
                    device.pending_area_id = winner.area_id
                    device.pending_floor_id = winner_floor_id
                    device.pending_streak = 1
                    device.pending_last_stamps = dict(current_stamps)
            else:
                device.pending_area_id = winner.area_id
                device.pending_floor_id = winner_floor_id
                device.pending_streak = 1
                device.pending_last_stamps = dict(current_stamps)
        else:
            device.pending_area_id = winner.area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1
            device.pending_last_stamps = dict(current_stamps)

        if device.pending_streak >= streak_target:
            device.reset_pending_state()
            _apply_selection(winner)
        else:
            device.diag_area_switch = tests.sensortext()
            _apply_selection(device.area_advert)
