"""
Auto-calibration for Bermuda BLE scanners.

This module provides automatic RSSI offset calculation based on scanner-to-scanner
visibility. When scanners can see each other (e.g., Shelly devices broadcasting as
iBeacons), we can calculate relative receiver sensitivity differences.

Principle:
- If Scanner A sees Scanner B with RSSI -55 dBm
- And Scanner B sees Scanner A with RSSI -65 dBm
- The 10 dB difference indicates receiver asymmetry
- Scanner A receives 5 dB stronger than average, Scanner B 5 dB weaker

This is similar to GPS differential correction, where known reference points
are used to improve accuracy.

The filtering uses Kalman filters for optimal RSSI smoothing based on research
from Wouter Bulten and PMC5461075 (BLE Indoor Localization).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse

from .filters import (
    CALIBRATION_HYSTERESIS_DB,
    CALIBRATION_MAX_HISTORY,
    CALIBRATION_MIN_PAIRS,
    CALIBRATION_MIN_SAMPLES,
    CALIBRATION_SCANNER_TIMEOUT,
    KalmanFilter,
)

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class ScannerPairData:
    """
    Data for a scanner pair's cross-visibility.

    Uses Kalman filters for optimal RSSI smoothing based on research
    (R=0.008 process noise, Q=4.0 measurement noise for BLE RSSI).
    """

    scanner_a: str
    scanner_b: str
    # Kalman filters for smoothing raw RSSI
    kalman_ab: KalmanFilter = field(default_factory=KalmanFilter)
    kalman_ba: KalmanFilter = field(default_factory=KalmanFilter)
    # Raw RSSI history (kept for diagnostics only)
    rssi_history_ab: list[float] = field(default_factory=list)  # When A sees B
    rssi_history_ba: list[float] = field(default_factory=list)  # When B sees A
    # Last update timestamps for each direction (for staleness tracking)
    last_update_ab: float | None = None
    last_update_ba: float | None = None

    @property
    def rssi_a_sees_b(self) -> float | None:
        """Kalman-filtered RSSI when A sees B (optimal smoothing)."""
        if self.kalman_ab.sample_count < CALIBRATION_MIN_SAMPLES:
            return None
        return self.kalman_ab.get_estimate()

    @property
    def rssi_b_sees_a(self) -> float | None:
        """Kalman-filtered RSSI when B sees A (optimal smoothing)."""
        if self.kalman_ba.sample_count < CALIBRATION_MIN_SAMPLES:
            return None
        return self.kalman_ba.get_estimate()

    @property
    def sample_count_ab(self) -> int:
        """Number of samples for A seeing B."""
        return self.kalman_ab.sample_count

    @property
    def sample_count_ba(self) -> int:
        """Number of samples for B seeing A."""
        return self.kalman_ba.sample_count

    @property
    def has_bidirectional_data(self) -> bool:
        """Return True if both scanners can see each other with enough samples."""
        return (
            self.kalman_ab.sample_count >= CALIBRATION_MIN_SAMPLES
            and self.kalman_ba.sample_count >= CALIBRATION_MIN_SAMPLES
        )

    @property
    def rssi_difference(self) -> float | None:
        """
        Calculate RSSI difference between the two directions.

        Positive value means A receives stronger than B.
        Returns None if bidirectional data is not available.
        """
        if not self.has_bidirectional_data:
            return None
        rssi_ab = self.rssi_a_sees_b
        rssi_ba = self.rssi_b_sees_a
        if rssi_ab is None or rssi_ba is None:
            return None  # pragma: no cover
        return rssi_ab - rssi_ba


@dataclass
class ScannerCalibrationManager:
    """
    Manages automatic scanner calibration based on cross-visibility.

    This class tracks RSSI measurements between scanners and calculates
    suggested RSSI offsets to compensate for receiver sensitivity differences.
    """

    # Cross-visibility data: {(scanner_a, scanner_b): ScannerPairData}
    # Keys are always ordered (min_addr, max_addr) to avoid duplicates
    scanner_pairs: dict[tuple[str, str], ScannerPairData] = field(default_factory=dict)

    # Calculated suggested offsets: {scanner_address: suggested_offset}
    suggested_offsets: dict[str, float] = field(default_factory=dict)

    # Track which scanners are active (have been seen recently)
    active_scanners: set[str] = field(default_factory=set)

    # Track last timestamp each scanner provided data (for offline detection)
    scanner_last_seen: dict[str, float] = field(default_factory=dict)

    def _get_pair_key(self, addr_a: str, addr_b: str) -> tuple[str, str]:
        """Get canonical key for scanner pair (always sorted)."""
        return (min(addr_a, addr_b), max(addr_a, addr_b))

    def _get_or_create_pair(self, addr_a: str, addr_b: str) -> ScannerPairData:
        """Get or create pair data for two scanners."""
        key = self._get_pair_key(addr_a, addr_b)
        if key not in self.scanner_pairs:
            self.scanner_pairs[key] = ScannerPairData(scanner_a=key[0], scanner_b=key[1])
        return self.scanner_pairs[key]

    def _is_scanner_online(self, scanner_addr: str, nowstamp: float) -> bool:
        """
        Check if a scanner is considered online (has provided data recently).

        A scanner is online if it has been seen within CALIBRATION_SCANNER_TIMEOUT.
        Offline scanners are excluded from offset calculations to prevent stale
        data from affecting calibration when a scanner is moved or replaced.

        Args:
            scanner_addr: Address of the scanner to check.
            nowstamp: Current monotonic timestamp.

        Returns:
            True if scanner has been seen within timeout, False otherwise.

        """
        last_seen = self.scanner_last_seen.get(scanner_addr)
        if last_seen is None:
            return False
        return (nowstamp - last_seen) < CALIBRATION_SCANNER_TIMEOUT

    def update_cross_visibility(
        self,
        receiver_addr: str,
        sender_addr: str,
        rssi_raw: float,
        timestamp: float | None = None,
    ) -> None:
        """
        Update cross-visibility data when a scanner sees another scanner.

        Uses Kalman filter for optimal RSSI smoothing with time-aware
        process noise scaling.

        Args:
            receiver_addr: Address of the scanner that received the signal
            sender_addr: Address of the scanner that sent the signal (as iBeacon)
            rssi_raw: RAW RSSI value (NOT adjusted by rssi_offset!)
                     Using raw RSSI is critical to avoid circular calibration.
            timestamp: Monotonic timestamp for time-aware Kalman filtering.
                      If None, uses monotonic_time_coarse().

        """
        ts = timestamp if timestamp is not None else monotonic_time_coarse()
        pair = self._get_or_create_pair(receiver_addr, sender_addr)

        # Add to history and update Kalman filter with timestamp for dt calculation
        if receiver_addr == pair.scanner_a:
            # A sees B
            pair.rssi_history_ab.append(rssi_raw)
            if len(pair.rssi_history_ab) > CALIBRATION_MAX_HISTORY:
                pair.rssi_history_ab.pop(0)
            pair.kalman_ab.update(rssi_raw, timestamp=ts)
            pair.last_update_ab = ts
        else:
            # B sees A
            pair.rssi_history_ba.append(rssi_raw)
            if len(pair.rssi_history_ba) > CALIBRATION_MAX_HISTORY:
                pair.rssi_history_ba.pop(0)
            pair.kalman_ba.update(rssi_raw, timestamp=ts)
            pair.last_update_ba = ts

        # Track scanner activity for staleness detection
        self.scanner_last_seen[receiver_addr] = ts
        self.scanner_last_seen[sender_addr] = ts
        self.active_scanners.add(receiver_addr)
        self.active_scanners.add(sender_addr)

    def calculate_suggested_offsets(self, nowstamp: float | None = None) -> dict[str, float]:
        """
        Calculate suggested RSSI offsets from scanner cross-visibility.

        Algorithm:
        1. For each scanner pair with bidirectional data, calculate the RSSI difference
        2. Skip pairs where either scanner is offline (no data for > 5 minutes)
        3. The difference / 2 gives the relative offset for each scanner
        4. Average all pair-based offsets for each scanner
        5. Round to integer dB values

        Args:
            nowstamp: Current timestamp for online checking. If None, uses
                     monotonic_time_coarse(). Exposed for testing.

        Returns:
            Dictionary mapping scanner addresses to suggested RSSI offsets

        """
        if nowstamp is None:
            nowstamp = monotonic_time_coarse()

        # Collect offset contributions for each scanner
        offset_contributions: dict[str, list[float]] = {addr: [] for addr in self.active_scanners}

        bidirectional_pairs = 0
        offline_pairs_skipped = 0

        for pair in self.scanner_pairs.values():
            # Skip pairs where either scanner is offline
            if not self._is_scanner_online(pair.scanner_a, nowstamp):
                _LOGGER.debug(
                    "Auto-cal pair %s <-> %s: skipped - scanner %s offline",
                    pair.scanner_a,
                    pair.scanner_b,
                    pair.scanner_a,
                )
                offline_pairs_skipped += 1
                continue
            if not self._is_scanner_online(pair.scanner_b, nowstamp):
                _LOGGER.debug(
                    "Auto-cal pair %s <-> %s: skipped - scanner %s offline",
                    pair.scanner_a,
                    pair.scanner_b,
                    pair.scanner_b,
                )
                offline_pairs_skipped += 1
                continue

            diff = pair.rssi_difference
            if diff is None:
                _LOGGER.debug(
                    "Auto-cal pair %s <-> %s: not bidirectional yet (A sees B: %s/%d, B sees A: %s/%d)",
                    pair.scanner_a,
                    pair.scanner_b,
                    pair.rssi_a_sees_b,
                    pair.sample_count_ab,
                    pair.rssi_b_sees_a,
                    pair.sample_count_ba,
                )
                continue

            bidirectional_pairs += 1
            _LOGGER.debug(
                "Auto-cal pair %s <-> %s: bidirectional! diff=%.1f dB",
                pair.scanner_a,
                pair.scanner_b,
                diff,
            )
            # Positive diff means A receives stronger → A needs negative offset
            # to bring its readings down to match B's perspective
            offset_contributions[pair.scanner_a].append(-diff / 2)
            offset_contributions[pair.scanner_b].append(diff / 2)

        if offline_pairs_skipped > 0:
            _LOGGER.debug(
                "Auto-cal: Skipped %d pairs due to offline scanners",
                offline_pairs_skipped,
            )
        if bidirectional_pairs > 0:
            _LOGGER.debug("Auto-cal: Found %d bidirectional pairs", bidirectional_pairs)

        # Calculate median offset for each scanner with hysteresis
        for addr, contributions in offset_contributions.items():
            if len(contributions) >= CALIBRATION_MIN_PAIRS:
                # Use median for robustness against outliers
                median_offset = statistics.median(contributions)
                # Round to nearest integer dB
                new_offset = round(median_offset)

                # Apply hysteresis: only update if change exceeds threshold
                current_offset = self.suggested_offsets.get(addr)
                if current_offset is None:
                    # First time seeing this scanner - accept initial value
                    self.suggested_offsets[addr] = new_offset
                    _LOGGER.debug(
                        "Auto-cal: Initial offset for %s: %d dB (from %d pairs)",
                        addr,
                        new_offset,
                        len(contributions),
                    )
                elif abs(new_offset - current_offset) >= CALIBRATION_HYSTERESIS_DB:
                    # Significant change - update offset
                    _LOGGER.info(
                        "Auto-cal: Offset for %s changed: %d → %d dB (from %d pairs)",
                        addr,
                        current_offset,
                        new_offset,
                        len(contributions),
                    )
                    self.suggested_offsets[addr] = new_offset
                else:
                    # Change within hysteresis band - keep current value
                    _LOGGER.debug(
                        "Auto-cal: Offset for %s stable at %d dB (candidate: %d, hysteresis: %d dB)",
                        addr,
                        current_offset,
                        new_offset,
                        CALIBRATION_HYSTERESIS_DB,
                    )

        # Log summary of scanner pair status for diagnostics
        if self.scanner_pairs:
            pairs_summary = []
            for pair in self.scanner_pairs.values():
                status = "✓" if pair.has_bidirectional_data else "✗"
                pairs_summary.append(
                    f"{pair.scanner_a[:8]}↔{pair.scanner_b[:8]}: "
                    f"AB={pair.sample_count_ab} BA={pair.sample_count_ba} [{status}]"
                )
            _LOGGER.debug(
                "Auto-cal: Scanner pair summary (%d pairs): %s",
                len(self.scanner_pairs),
                "; ".join(pairs_summary[:10]),  # Show first 10
            )

        # Log which scanners got offsets and which didn't
        if self.active_scanners:
            scanners_with_offsets = set(self.suggested_offsets.keys())
            scanners_without_offsets = self.active_scanners - scanners_with_offsets
            if scanners_without_offsets:
                _LOGGER.debug(
                    "Auto-cal: %d scanners without suggested offsets: %s",
                    len(scanners_without_offsets),
                    list(scanners_without_offsets),
                )

        return self.suggested_offsets

    def get_scanner_pair_info(self, nowstamp: float | None = None) -> list[dict[str, Any]]:
        """
        Get human-readable info about scanner pairs for diagnostics.

        Args:
            nowstamp: Current timestamp for online checking. If None, uses
                     monotonic_time_coarse(). Exposed for testing.

        Returns:
            List of dictionaries with pair information including Kalman filter
            diagnostics and online status.

        """
        if nowstamp is None:
            nowstamp = monotonic_time_coarse()
        info: list[dict[str, Any]] = []
        for pair in self.scanner_pairs.values():
            pair_info = {
                "scanner_a": pair.scanner_a,
                "scanner_b": pair.scanner_b,
                "rssi_a_sees_b": pair.rssi_a_sees_b,
                "rssi_b_sees_a": pair.rssi_b_sees_a,
                "samples_ab": pair.sample_count_ab,
                "samples_ba": pair.sample_count_ba,
                "bidirectional": pair.has_bidirectional_data,
                "difference": pair.rssi_difference,
                # Kalman filter diagnostics
                "kalman_ab": pair.kalman_ab.get_diagnostics(),
                "kalman_ba": pair.kalman_ba.get_diagnostics(),
                # Online status and timestamps
                "scanner_a_online": self._is_scanner_online(pair.scanner_a, nowstamp),
                "scanner_b_online": self._is_scanner_online(pair.scanner_b, nowstamp),
                "last_update_ab": pair.last_update_ab,
                "last_update_ba": pair.last_update_ba,
            }
            info.append(pair_info)
        return info

    def clear(self) -> None:
        """Clear all calibration data including staleness tracking."""
        self.scanner_pairs.clear()
        self.suggested_offsets.clear()
        self.active_scanners.clear()
        self.scanner_last_seen.clear()


def update_scanner_calibration(  # noqa: C901
    calibration_manager: ScannerCalibrationManager,
    scanner_list: set[str],
    devices: dict[str, BermudaDevice],
) -> dict[str, float]:
    """
    Update scanner calibration based on current device data.

    This function should be called periodically (e.g., each update cycle)
    to refresh cross-visibility data and recalculate suggested offsets.

    Args:
        calibration_manager: The calibration manager instance
        scanner_list: Set of scanner addresses
        devices: Dictionary of all BermudaDevice instances

    Returns:
        Dictionary of suggested RSSI offsets per scanner

    """
    # Get timestamp once for consistent staleness tracking
    nowstamp = monotonic_time_coarse()

    # Build a reverse lookup: any_scanner_mac -> canonical_scanner_address
    # Scanners may have multiple MAC addresses (WiFi, BLE, Ethernet) and the
    # iBeacon broadcasts come from the BLE MAC, which may differ from the
    # canonical scanner address in scanner_list.
    mac_to_scanner: dict[str, str] = {}
    for scanner_addr in scanner_list:
        scanner_device = devices.get(scanner_addr)
        if scanner_device is None:
            # Scanner not in devices dict yet, just use canonical address
            mac_to_scanner[scanner_addr] = scanner_addr
            continue

        # Add all possible MAC addresses that could identify this scanner
        # The canonical address
        mac_to_scanner[scanner_addr] = scanner_addr

        # BLE MAC (may differ from canonical address for ESPHome/Shelly)
        if hasattr(scanner_device, "address_ble_mac") and scanner_device.address_ble_mac:
            mac_to_scanner[scanner_device.address_ble_mac] = scanner_addr

        # WiFi MAC
        if hasattr(scanner_device, "address_wifi_mac") and scanner_device.address_wifi_mac:
            mac_to_scanner[scanner_device.address_wifi_mac] = scanner_addr

        # Scanner's own metadevice_sources (potential BLE MACs it broadcasts from)
        if hasattr(scanner_device, "metadevice_sources") and scanner_device.metadevice_sources:
            for source_mac in scanner_device.metadevice_sources:
                mac_to_scanner[source_mac] = scanner_addr

    # Build lookup: canonical_scanner_address -> list of iBeacon addresses
    scanner_to_ibeacon: dict[str, list[str]] = {addr: [] for addr in scanner_list}
    for device_addr, device in devices.items():
        if hasattr(device, "metadevice_sources") and device.metadevice_sources:
            for source_mac in device.metadevice_sources:
                # Look up which scanner this MAC belongs to
                canonical_scanner = mac_to_scanner.get(source_mac)
                if canonical_scanner is not None:
                    scanner_to_ibeacon[canonical_scanner].append(device_addr)

    # Log which scanners have iBeacon broadcasts
    scanners_with_ibeacons = {k: v for k, v in scanner_to_ibeacon.items() if v}
    scanners_without_ibeacons = [k for k in scanner_list if k not in scanners_with_ibeacons]

    if scanners_with_ibeacons:
        _LOGGER.debug(
            "Auto-cal: Found %d scanners with iBeacon broadcasts: %s",
            len(scanners_with_ibeacons),
            scanners_with_ibeacons,
        )
    if scanners_without_ibeacons:
        _LOGGER.debug(
            "Auto-cal: %d scanners WITHOUT iBeacon broadcasts: %s",
            len(scanners_without_ibeacons),
            scanners_without_ibeacons,
        )

    visibility_found = 0
    visibility_not_found = 0

    for scanner_addr in scanner_list:
        # Check if this scanner sees any other scanners
        for other_addr in scanner_list:
            if other_addr == scanner_addr:
                continue

            # Check if scanner_addr has received an advert from other_addr
            # IMPORTANT: adverts are stored on the SENDING device, not the receiver!
            # The key is (source_mac, receiver_scanner_addr)
            advert = None

            # Method 1: Direct MAC match - check if other_addr device was seen by scanner_addr
            other_device = devices.get(other_addr)
            if other_device is not None:
                # Look for advert where other_device was seen by scanner_addr
                for advert_key, adv in other_device.adverts.items():
                    if advert_key[1] == scanner_addr:
                        advert = adv
                        _LOGGER.debug(
                            "Auto-cal: Scanner %s sees scanner %s directly (key: %s)",
                            scanner_addr,
                            other_addr,
                            advert_key,
                        )
                        break

            # Method 2: Check if any iBeacon/metadevice sourced from other_addr
            # was seen by scanner_addr (ESPHome iBeacons use UUID as address)
            if advert is None:
                ibeacon_addrs = scanner_to_ibeacon.get(other_addr, [])
                if not ibeacon_addrs:
                    _LOGGER.debug(
                        "Auto-cal: Scanner %s has no iBeacon devices mapped",
                        other_addr,
                    )
                for ibeacon_addr in ibeacon_addrs:
                    ibeacon_device = devices.get(ibeacon_addr)
                    if ibeacon_device is None:
                        _LOGGER.debug(
                            "Auto-cal: iBeacon device %s not found in devices dict",
                            ibeacon_addr,
                        )
                        continue
                    # Look for advert where iBeacon was seen by scanner_addr
                    advert_keys = list(ibeacon_device.adverts.keys())
                    matching_keys = [k for k in advert_keys if k[1] == scanner_addr]
                    if not matching_keys and advert_keys:
                        _LOGGER.debug(
                            "Auto-cal: iBeacon %s has %d adverts, none from scanner %s. Advert scanner addresses: %s",
                            ibeacon_addr,
                            len(advert_keys),
                            scanner_addr,
                            [k[1] for k in advert_keys[:5]],  # Show first 5
                        )
                    for advert_key, adv in ibeacon_device.adverts.items():
                        if advert_key[1] == scanner_addr:
                            advert = adv
                            _LOGGER.debug(
                                "Auto-cal: Scanner %s sees scanner %s via iBeacon %s",
                                scanner_addr,
                                other_addr,
                                ibeacon_addr,
                            )
                            break
                    if advert is not None:
                        break

            if advert is None:
                visibility_not_found += 1
                continue

            visibility_found += 1

            # IMPORTANT: Use RAW RSSI for calibration, NOT rssi_filtered!
            # rssi_filtered includes conf_rssi_offset which would create a
            # circular dependency (calibration based on already-calibrated values).
            rssi_raw = advert.rssi

            if rssi_raw is None:
                continue

            calibration_manager.update_cross_visibility(
                receiver_addr=scanner_addr,
                sender_addr=other_addr,
                rssi_raw=rssi_raw,
                timestamp=nowstamp,
            )

    if visibility_found > 0 or visibility_not_found > 0:
        _LOGGER.debug(
            "Auto-cal: Visibility check complete - found: %d, not found: %d",
            visibility_found,
            visibility_not_found,
        )

    # Recalculate suggested offsets
    return calibration_manager.calculate_suggested_offsets()
