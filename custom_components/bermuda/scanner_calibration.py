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
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice

_LOGGER = logging.getLogger(__name__)

# Minimum samples before we trust cross-visibility data
MIN_CROSS_VISIBILITY_SAMPLES = 5

# Minimum scanner pairs needed to calculate an offset
MIN_SCANNER_PAIRS = 1


@dataclass
class ScannerPairData:
    """Data for a scanner pair's cross-visibility."""

    scanner_a: str
    scanner_b: str
    rssi_a_sees_b: float | None = None  # Kalman-filtered RSSI when A sees B
    rssi_b_sees_a: float | None = None  # Kalman-filtered RSSI when B sees A
    sample_count_ab: int = 0  # How many times A has seen B
    sample_count_ba: int = 0  # How many times B has seen A

    @property
    def has_bidirectional_data(self) -> bool:
        """Return True if both scanners can see each other."""
        return (
            self.rssi_a_sees_b is not None
            and self.rssi_b_sees_a is not None
            and self.sample_count_ab >= MIN_CROSS_VISIBILITY_SAMPLES
            and self.sample_count_ba >= MIN_CROSS_VISIBILITY_SAMPLES
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
        # Explicit None check for type narrowing (has_bidirectional_data guarantees these)
        if self.rssi_a_sees_b is None or self.rssi_b_sees_a is None:
            return None  # pragma: no cover - defensive check for type safety
        return self.rssi_a_sees_b - self.rssi_b_sees_a


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
        rssi_filtered: float,
        sample_count: int = 1,
    ) -> None:
        """
        Update cross-visibility data when a scanner sees another scanner.

        Args:
            receiver_addr: Address of the scanner that received the signal
            sender_addr: Address of the scanner that sent the signal (as iBeacon)
            rssi_filtered: Kalman-filtered RSSI value
            sample_count: Number of samples this reading is based on

        """
        pair = self._get_or_create_pair(receiver_addr, sender_addr)

        # Update the correct direction based on which scanner is receiver
        if receiver_addr == pair.scanner_a:
            # A sees B
            pair.rssi_a_sees_b = rssi_filtered
            pair.sample_count_ab = sample_count
        else:
            # B sees A
            pair.rssi_b_sees_a = rssi_filtered
            pair.sample_count_ba = sample_count

        self.active_scanners.add(receiver_addr)
        self.active_scanners.add(sender_addr)

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

        for pair in self.scanner_pairs.values():
            diff = pair.rssi_difference
            if diff is None:
                continue

            # Positive diff means A receives stronger → A needs negative offset
            # to bring its readings down to match B's perspective
            offset_contributions[pair.scanner_a].append(-diff / 2)
            offset_contributions[pair.scanner_b].append(diff / 2)

        # Calculate median offset for each scanner
        result: dict[str, float] = {}
        for addr, contributions in offset_contributions.items():
            if len(contributions) >= MIN_SCANNER_PAIRS:
                # Use median for robustness against outliers
                median_offset = statistics.median(contributions)
                # Round to nearest integer dB
                result[addr] = round(median_offset)

        self.suggested_offsets = result
        return result

    def get_scanner_pair_info(self) -> list[dict[str, Any]]:
        """
        Get human-readable info about scanner pairs for diagnostics.

        Returns:
            List of dictionaries with pair information

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
    for scanner_addr in scanner_list:
        scanner_device = devices.get(scanner_addr)
        if scanner_device is None:
            continue

        # Check if this scanner sees any other scanners
        for other_addr in scanner_list:
            if other_addr == scanner_addr:
                continue

            # Check if scanner has an advert from the other scanner
            # adverts dict has tuple keys: (device_addr, scanner_addr)
            # where device_addr is the sender and scanner_addr is the receiver
            advert = None

            # Direct MAC match - look for tuple key (other_addr, scanner_addr)
            advert_key = (other_addr, scanner_addr)
            if advert_key in scanner_device.adverts:
                advert = scanner_device.adverts[advert_key]

            # Also check metadevice_sources for the other scanner
            # (Shelly devices may broadcast with different MACs)
            if advert is None:
                other_device = devices.get(other_addr)
                if other_device is not None:
                    for source_addr in getattr(other_device, "metadevice_sources", []):
                        source_key = (source_addr, scanner_addr)
                        if source_key in scanner_device.adverts:
                            advert = scanner_device.adverts[source_key]
                            break

            if advert is None:
                continue

            # Use Kalman-filtered RSSI if available
            rssi_filtered = advert.rssi_filtered
            if rssi_filtered is None:
                # Fall back to raw RSSI if Kalman not initialized yet
                rssi_filtered = advert.rssi

            if rssi_filtered is None:
                continue

            # Count samples from history
            sample_count = len(getattr(advert, "hist_rssi", [])) or 1

            calibration_manager.update_cross_visibility(
                receiver_addr=scanner_addr,
                sender_addr=other_addr,
                rssi_filtered=rssi_filtered,
                sample_count=sample_count,
            )

    # Recalculate suggested offsets
    return calibration_manager.calculate_suggested_offsets()
