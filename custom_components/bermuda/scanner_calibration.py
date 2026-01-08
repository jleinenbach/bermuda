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

The filtering components are provided by the `filters` submodule, which offers:
- AdaptiveStatistics: Online mean/variance with CUSUM changepoint detection
- Research-backed constants for BLE RSSI processing
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .filters import (
    AdaptiveStatistics,
    CALIBRATION_HYSTERESIS_DB,
    CALIBRATION_MAX_HISTORY,
    CALIBRATION_MIN_PAIRS,
    CALIBRATION_MIN_SAMPLES,
)

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class ScannerPairData:
    """Data for a scanner pair's cross-visibility."""

    scanner_a: str
    scanner_b: str
    # Adaptive statistics for each direction (primary source for RSSI values)
    # Uses EMA for smooth, stable estimates with CUSUM changepoint detection
    stats_ab: AdaptiveStatistics = field(default_factory=AdaptiveStatistics)
    stats_ba: AdaptiveStatistics = field(default_factory=AdaptiveStatistics)
    # Raw RSSI history (kept for diagnostics, not used for offset calculation)
    rssi_history_ab: list[float] = field(default_factory=list)  # When A sees B
    rssi_history_ba: list[float] = field(default_factory=list)  # When B sees A

    @property
    def rssi_a_sees_b(self) -> float | None:
        """EMA-filtered RSSI when A sees B (more stable than raw median)."""
        if self.stats_ab.sample_count < CALIBRATION_MIN_SAMPLES:
            return None
        return self.stats_ab.mean

    @property
    def rssi_b_sees_a(self) -> float | None:
        """EMA-filtered RSSI when B sees A (more stable than raw median)."""
        if self.stats_ba.sample_count < CALIBRATION_MIN_SAMPLES:
            return None
        return self.stats_ba.mean

    @property
    def sample_count_ab(self) -> int:
        """Number of samples for A seeing B."""
        return self.stats_ab.sample_count

    @property
    def sample_count_ba(self) -> int:
        """Number of samples for B seeing A."""
        return self.stats_ba.sample_count

    @property
    def has_bidirectional_data(self) -> bool:
        """Return True if both scanners can see each other with enough samples."""
        return (
            self.stats_ab.sample_count >= CALIBRATION_MIN_SAMPLES
            and self.stats_ba.sample_count >= CALIBRATION_MIN_SAMPLES
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

    def _get_pair_key(self, addr_a: str, addr_b: str) -> tuple[str, str]:
        """Get canonical key for scanner pair (always sorted)."""
        return (min(addr_a, addr_b), max(addr_a, addr_b))

    def _get_or_create_pair(self, addr_a: str, addr_b: str) -> ScannerPairData:
        """Get or create pair data for two scanners."""
        key = self._get_pair_key(addr_a, addr_b)
        if key not in self.scanner_pairs:
            self.scanner_pairs[key] = ScannerPairData(scanner_a=key[0], scanner_b=key[1])
        return self.scanner_pairs[key]

    def update_cross_visibility(
        self,
        receiver_addr: str,
        sender_addr: str,
        rssi_raw: float,
    ) -> bool:
        """
        Update cross-visibility data when a scanner sees another scanner.

        Args:
            receiver_addr: Address of the scanner that received the signal
            sender_addr: Address of the scanner that sent the signal (as iBeacon)
            rssi_raw: RAW RSSI value (NOT adjusted by rssi_offset!)
                     Using raw RSSI is critical to avoid circular calibration.

        Returns:
            True if a changepoint was detected (significant shift in RSSI),
            indicating potential hardware or environmental change.
        """
        pair = self._get_or_create_pair(receiver_addr, sender_addr)
        changepoint_detected = False

        # Add to history, keeping a rolling window
        if receiver_addr == pair.scanner_a:
            # A sees B
            pair.rssi_history_ab.append(rssi_raw)
            if len(pair.rssi_history_ab) > CALIBRATION_MAX_HISTORY:
                pair.rssi_history_ab.pop(0)
            # Update adaptive statistics and check for changepoint
            changepoint_detected = pair.stats_ab.update(rssi_raw)
        else:
            # B sees A
            pair.rssi_history_ba.append(rssi_raw)
            if len(pair.rssi_history_ba) > CALIBRATION_MAX_HISTORY:
                pair.rssi_history_ba.pop(0)
            # Update adaptive statistics and check for changepoint
            changepoint_detected = pair.stats_ba.update(rssi_raw)

        if changepoint_detected:
            _LOGGER.info(
                "Auto-cal: Changepoint detected for %s -> %s, "
                "RSSI characteristics may have changed",
                receiver_addr,
                sender_addr,
            )

        self.active_scanners.add(receiver_addr)
        self.active_scanners.add(sender_addr)
        return changepoint_detected

    def calculate_suggested_offsets(self) -> dict[str, float]:
        """
        Calculate suggested RSSI offsets from scanner cross-visibility.

        Algorithm:
        1. For each scanner pair with bidirectional data, calculate the RSSI difference
        2. The difference / 2 gives the relative offset for each scanner
        3. Average all pair-based offsets for each scanner
        4. Round to integer dB values

        Returns:
            Dictionary mapping scanner addresses to suggested RSSI offsets
        """
        # Collect offset contributions for each scanner
        offset_contributions: dict[str, list[float]] = {
            addr: [] for addr in self.active_scanners
        }

        bidirectional_pairs = 0
        for pair in self.scanner_pairs.values():
            diff = pair.rssi_difference
            if diff is None:
                _LOGGER.debug(
                    "Auto-cal pair %s <-> %s: not bidirectional yet "
                    "(A sees B: %s/%d, B sees A: %s/%d)",
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
                        "Auto-cal: Offset for %s stable at %d dB "
                        "(candidate: %d, hysteresis: %d dB)",
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
                _LOGGER.info(
                    "Auto-cal: %d scanners WITHOUT suggested offsets: %s",
                    len(scanners_without_offsets),
                    list(scanners_without_offsets),
                )

        return self.suggested_offsets

    def get_scanner_pair_info(self) -> list[dict[str, Any]]:
        """
        Get human-readable info about scanner pairs for diagnostics.

        Returns:
            List of dictionaries with pair information including adaptive statistics.
        """
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
                # Adaptive statistics for monitoring stability
                "stats_ab": pair.stats_ab.to_dict(),
                "stats_ba": pair.stats_ba.to_dict(),
            }
            info.append(pair_info)
        return info

    def clear(self) -> None:
        """Clear all calibration data."""
        self.scanner_pairs.clear()
        self.suggested_offsets.clear()
        self.active_scanners.clear()


def update_scanner_calibration(
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
    # Build a reverse lookup: scanner_mac -> list of iBeacon/metadevice addresses
    # that broadcast from that scanner
    scanner_to_ibeacon: dict[str, list[str]] = {addr: [] for addr in scanner_list}
    for device_addr, device in devices.items():
        if hasattr(device, "metadevice_sources") and device.metadevice_sources:
            for source_mac in device.metadevice_sources:
                if source_mac in scanner_to_ibeacon:
                    scanner_to_ibeacon[source_mac].append(device_addr)

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
                            "Auto-cal: iBeacon %s has %d adverts, none from scanner %s. "
                            "Advert scanner addresses: %s",
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
            )

    if visibility_found > 0 or visibility_not_found > 0:
        _LOGGER.debug(
            "Auto-cal: Visibility check complete - found: %d, not found: %d",
            visibility_found,
            visibility_not_found,
        )

    # Recalculate suggested offsets
    return calibration_manager.calculate_suggested_offsets()
