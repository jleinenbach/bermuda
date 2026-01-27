"""
Bermuda's internal representation of a device to scanner relationship.

This can also be thought of as the representation of an advertisement
received by a given scanner, in that it's the advert that links the
device to a scanner. Multiple scanners will receive a given advert, but
each receiver experiences it (well, the rssi) uniquely.

Every bluetooth scanner is a BermudaDevice, but this class
is the nested entry that gets attached to each device's `scanners`
dict. It is a sub-set of a 'device' and will have attributes specific
to the combination of the scanner and the device it is reporting.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Final

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    AREA_MAX_AD_AGE_DEFAULT,
    AREA_MAX_AD_AGE_LIMIT,
    CONF_ATTENUATION,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    DEFAULT_ATTENUATION,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DISTANCE_INFINITE,
    HIST_KEEP_COUNT,
    MAX_DISTANCE_VARIANCE,
    MIN_DISTANCE_FOR_VARIANCE,
    NEAR_FIELD_DISTANCE_VARIANCE,
    PATH_LOSS_EXPONENT_NEAR,
    RSSI_HISTORY_SAMPLES,
    TWO_SLOPE_BREAKPOINT_METRES,
    VARIANCE_COLD_START_SAMPLES,
    VARIANCE_FALLBACK_UNINIT,
    VARIANCE_FLOOR_COLD_START,
    VARIANCE_FLOOR_CONVERGED,
    VELOCITY_NOISE_MULTIPLIER,
    VELOCITY_TELEPORT_THRESHOLD,
)
from .filters import KalmanFilter
from .util import clean_charbuf, rssi_to_metres

if TYPE_CHECKING:
    from bleak.backends.scanner import AdvertisementData

    from .bermuda_device import BermudaDevice

# The if instead of min/max triggers PLR1730, but when
# split over two lines, ruff removes it, then complains again.
# so we're just disabling it for the whole file.
# https://github.com/astral-sh/ruff/issues/4244


class BermudaAdvert(dict[str, Any]):
    """
    Represents details from a scanner relevant to a specific device.

    Effectively a link between two BermudaDevices, being the tracked device
    and the scanner device. So each transmitting device will have a collection
    of these BermudaDeviceScanner entries, one for each scanner that has picked
    up the advertisement.

    This is created (and updated) by the receipt of an advertisement, which represents
    a BermudaDevice hearing an advert from another BermudaDevice, if that makes sense!

    A BermudaDevice's "adverts" property will contain one of these for each
    scanner that has "seen" it.

    The advert object only stores signal history and metadata; resolution of
    protocol-specific payloads (such as Google FMDN EIDs) is performed in the
    coordinator to avoid redundant parsing on the hot path.

    """

    def __hash__(self) -> int:  # type: ignore[override]
        """The device-mac / scanner mac uniquely identifies a received advertisement pair."""
        return hash((self.device_address, self.scanner_address))

    @property
    def conf_rssi_offset(self) -> int:
        """
        Get RSSI offset for this scanner from options.

        Reads dynamically from options to ensure settings changes take effect immediately.
        """
        rssi_offsets: dict[str, int] = self.options.get(CONF_RSSI_OFFSETS, {})
        return rssi_offsets.get(self.scanner_address, 0)

    @property
    def conf_ref_power(self) -> float | None:
        """
        Get reference power from options.

        Reads dynamically from options to ensure settings changes take effect immediately.
        """
        value: float | None = self.options.get(CONF_REF_POWER)
        return value

    @property
    def conf_attenuation(self) -> float | None:
        """
        Get attenuation from options.

        Reads dynamically from options to ensure settings changes take effect immediately.
        """
        value: float | None = self.options.get(CONF_ATTENUATION)
        return value

    @property
    def conf_max_velocity(self) -> float | None:
        """
        Get max velocity from options.

        Reads dynamically from options to ensure settings changes take effect immediately.
        """
        value: float | None = self.options.get(CONF_MAX_VELOCITY)
        return value

    @property
    def conf_smoothing_samples(self) -> int | None:
        """
        Get smoothing samples from options.

        Reads dynamically from options to ensure settings changes take effect immediately.
        """
        value: int | None = self.options.get(CONF_SMOOTHING_SAMPLES)
        return value

    def __init__(
        self,
        parent_device: BermudaDevice,  # The device being tracked
        advertisementdata: AdvertisementData,  # The advertisement info from the device, received by the scanner
        options: dict[str, Any],
        scanner_device: BermudaDevice,  # The scanner device that "saw" it.
        *,
        nowstamp: float | None = None,
    ) -> None:
        self.scanner_address: Final[str] = scanner_device.address
        self.device_address: Final[str] = parent_device.address
        self._device = parent_device
        self.ref_power: float = self._device.ref_power  # Take from parent at first, might be changed by metadevice l8r
        self.apply_new_scanner(scanner_device)

        self.options = options

        self.stamp: float = 0
        self.new_stamp: float | None = None  # Set when a new advert is loaded from update
        self.rssi: float | None = None
        self.tx_power: float | None = None
        self.rssi_distance: float | None = None
        self.rssi_distance_raw: float
        self.stale_update_count = 0  # How many times we did an update but no new stamps were found.
        self.adaptive_timeout: float = AREA_MAX_AD_AGE_DEFAULT  # Calculated based on device's ad pattern
        self.hist_stamp: list[float] = []
        self.hist_rssi: list[int] = []
        self.hist_distance: list[float] = []
        self.hist_distance_by_interval: list[float] = []  # updated per-interval
        self.hist_rssi_by_interval: list[float] = []  # Raw RSSI history for physical proximity checks
        self.hist_interval: list[float] = []  # WARNING: This is actually "age of ad when we polled"
        self.hist_velocity: list[float] = []  # Effective velocity versus previous stamped reading
        # FIX: Teleport Recovery - Counter for consecutive velocity-blocked measurements
        # When this counter reaches VELOCITY_TELEPORT_THRESHOLD, we accept the new
        # position anyway (self-healing to break the "velocity trap")
        self.velocity_blocked_count: int = 0
        # Note: conf_rssi_offset, conf_ref_power, conf_attenuation, conf_max_velocity,
        # and conf_smoothing_samples are now properties that read from self.options
        # dynamically. This ensures settings changes (including RSSI offsets) take
        # effect immediately without requiring a restart.
        self.local_name: list[tuple[str, bytes]] = []
        self.manufacturer_data: list[dict[int, bytes]] = []
        self.service_data: list[dict[str, bytes]] = []
        self.service_uuids: list[str] = []

        # Kalman filter for RSSI smoothing (scientific best practice for BLE distance estimation).
        # Applied to RSSI before distance calculation because RSSI is linear while distance is logarithmic.
        # Parameters based on BLE research: process_noise=1.0, measurement_noise=10.0
        # See: "The Influence of Kalman Filtering on RSSI" (2024) - 27% error reduction
        self.rssi_kalman: KalmanFilter = KalmanFilter(
            process_noise=1.0,  # Q: How much true RSSI can change between updates
            measurement_noise=10.0,  # R: RSSI variance (std dev ~3 dBm → variance ~10)
        )
        self.rssi_filtered: float | None = None  # Kalman-filtered RSSI value

        # Just pass the rest on to update...
        self.update_advertisement(advertisementdata, self.scanner_device, nowstamp=nowstamp)

    def apply_new_scanner(self, scanner_device: BermudaDevice) -> None:
        self.name: str = scanner_device.name  # or scandata.scanner.name
        self.scanner_device = scanner_device  # links to the source device
        if self.scanner_address != scanner_device.address:
            _LOGGER.error("Advert %s received new scanner with wrong address %s", self.__repr__(), scanner_device)
        self.area_id: str | None = scanner_device.area_id
        self.area_name: str | None = scanner_device.area_name
        # Only remote scanners log timestamps, local usb adaptors do not.
        self.scanner_sends_stamps = scanner_device.is_remote_scanner

    def update_advertisement(
        self, advertisementdata: AdvertisementData, scanner_device: BermudaDevice, *, nowstamp: float | None = None
    ) -> None:
        """
        Refresh the advert with the latest packet from a scanner.

        This method keeps the advert aligned with the scanner's current registry
        metadata (area/name), triggers a periodic registry refresh when the scanner
        has no area assignment, and updates RSSI history while respecting scanner
        stamp semantics. Distances and metadata are updated only when a genuinely
        new reading is observed.
        """
        if scanner_device is not self.scanner_device:
            _LOGGER.debug(
                "Replacing stale scanner device %s with %s", self.scanner_device.__repr__(), scanner_device.__repr__()
            )
            self.apply_new_scanner(scanner_device)

        if isinstance(self.scanner_device.area_id, str) and self.scanner_device.area_id != "":
            if self.scanner_device.area_id != self.area_id:
                self.area_id = self.scanner_device.area_id
                self.area_name = self.scanner_device.area_name

        stamp_now = nowstamp if nowstamp is not None else monotonic_time_coarse()

        if self.scanner_device.area_id is None:
            last_check: float = getattr(self.scanner_device, "last_devreg_check", 0.0)
            if stamp_now - last_check > 60:
                self.scanner_device.async_as_scanner_resolve_device_entries()  # type: ignore[no-untyped-call]
                self.scanner_device.last_devreg_check = stamp_now  # type: ignore[attr-defined]
                if isinstance(self.scanner_device.area_id, str) and self.scanner_device.area_id != "":
                    self.area_id = self.scanner_device.area_id
                    self.area_name = self.scanner_device.area_name

        scanner = self.scanner_device
        new_stamp: float | None = None

        if self.scanner_sends_stamps:
            new_stamp = scanner.async_as_scanner_get_stamp(self.device_address)
            if new_stamp is None:
                self.stale_update_count += 1
                return
            future_by = new_stamp - stamp_now
            if future_by > 0.5:
                self.stale_update_count += 1
                _LOGGER_SPAM_LESS.debug(
                    "future_stamp_advert",
                    "Ignoring future stamp for advert %s from scanner %s: stamp is in the future by %.3fs",
                    self.device_address,
                    self.scanner_address,
                    future_by,
                )
                return
            if self.stamp > new_stamp:
                self.stale_update_count += 1
                _LOGGER.debug("Advert from %s for %s is OLDER than last recorded", scanner.name, self._device.name)
                return
            if self.stamp == new_stamp:
                self.stale_update_count += 1
                return
        elif self.rssi != advertisementdata.rssi:
            new_stamp = stamp_now - 3.0
        else:
            return

        if new_stamp is not None and new_stamp > self.scanner_device.last_seen + 0.01:
            self.scanner_device.last_seen = new_stamp

        if len(self.hist_stamp) == 0 or new_stamp is not None:
            self.rssi = advertisementdata.rssi
            self.hist_rssi.insert(0, self.rssi)

            self._update_raw_distance(reading_is_new=True, timestamp=new_stamp)

            if new_stamp is not None and self.stamp is not None:
                _interval = new_stamp - self.stamp
            else:
                _interval = None
            self.hist_interval.insert(0, _interval)

            self.stamp = new_stamp or 0
            self.hist_stamp.insert(0, self.stamp)

        self.tx_power = advertisementdata.tx_power
        _want_name_update = False
        if advertisementdata.local_name is not None:
            nametuplet = (clean_charbuf(advertisementdata.local_name), advertisementdata.local_name.encode())
            if len(self.local_name) == 0 or self.local_name[0] != nametuplet:
                self.local_name.insert(0, nametuplet)
                del self.local_name[HIST_KEEP_COUNT:]
                if self._device.name_bt_local_name is None or len(self._device.name_bt_local_name) < len(nametuplet[0]):
                    self._device.name_bt_local_name = nametuplet[0]
                    _want_name_update = True

        if len(self.manufacturer_data) == 0 or self.manufacturer_data[0] != advertisementdata.manufacturer_data:
            self.manufacturer_data.insert(0, advertisementdata.manufacturer_data)
            self._device.process_manufacturer_data(self)
            _want_name_update = True
            del self.manufacturer_data[HIST_KEEP_COUNT:]

        if len(self.service_data) == 0 or self.service_data[0] != advertisementdata.service_data:
            self.service_data.insert(0, advertisementdata.service_data)
            if advertisementdata.service_data not in self.manufacturer_data[1:]:  # type: ignore[comparison-overlap]
                _want_name_update = True
            del self.service_data[HIST_KEEP_COUNT:]

        for service_uuid in advertisementdata.service_uuids:
            if service_uuid not in self.service_uuids:
                self.service_uuids.insert(0, service_uuid)
                _want_name_update = True
                del self.service_uuids[HIST_KEEP_COUNT:]

        if _want_name_update:
            self._device.make_name()

        self.new_stamp = new_stamp

    def _get_effective_ref_power(self) -> tuple[float, str]:
        """
        Determine the effective reference power for distance calculations.

        Priority order:
        1. Device-specific ref_power (user-calibrated per device)
        2. beacon_power from iBeacon advertisement (calibrated 1m RSSI)
        3. Global default ref_power from user configuration

        Note: BLE tx_power is NOT used here because it represents the transmitter's
        output power level (e.g., +8 dBm), NOT the expected RSSI at 1m (e.g., -55 dBm).
        These are fundamentally different values. Only iBeacon beacon_power is a
        calibrated 1m RSSI value.

        Returns
        -------
            Tuple of (ref_power value, source description string)

        """
        if self.ref_power != 0:
            return self.ref_power, "device-calibrated"
        beacon_power: float | None = getattr(self._device, "beacon_power", None)
        if beacon_power is not None:
            return beacon_power, "iBeacon beacon_power"
        ref_power = self.conf_ref_power
        if ref_power is None:
            ref_power = DEFAULT_REF_POWER
        return ref_power, "global config default"

    def _update_raw_distance(self, *, reading_is_new: bool = True, timestamp: float | None = None) -> float:
        """
        Converts rssi to raw distance and updates history stack and
        returns the new raw distance.

        reading_is_new should only be called by the regular update
        cycle, as it creates a new entry in the histories. Call with
        false if you just need to set / override distance measurements
        immediately, perhaps between cycles, in order to reflect a
        setting change (such as altering a device's ref_power setting).

        Args:
        ----
            reading_is_new: True if this is a new reading, False for overrides.
            timestamp: Measurement timestamp for time-aware Kalman filtering.
                       When provided, process noise scales with time delta,
                       so stale scanners have higher uncertainty.

        """
        ref_power, ref_power_source = self._get_effective_ref_power()
        if self.rssi is None:
            self.rssi_distance_raw = DISTANCE_INFINITE
            return DISTANCE_INFINITE
        adjusted_rssi = self.rssi + self.conf_rssi_offset

        # Apply adaptive Kalman filter to RSSI for improved distance estimation.
        # Uses RSSI-dependent measurement noise relative to device's ref_power.
        # This ensures correct behavior regardless of device TX power setting.
        # Scientific basis: SNR degrades with distance, so weaker signals have
        # higher variance and should influence the estimate less.
        # BUG FIX: Pass timestamp for time-aware process noise scaling.
        # Without this, stale scanners don't have increased uncertainty,
        # causing distant (stale) scanners to incorrectly "win" over close (fresh) ones.
        # Log invalid ref_power with device context for debugging
        if ref_power > 0:
            _LOGGER.warning(
                "Device %s has invalid ref_power %.1f dBm (from %s). "
                "This is likely TX power being used as RSSI-at-1m. "
                "Check beacon_power field or device configuration.",
                self._device.name or self._device.address,
                ref_power,
                ref_power_source,
            )
        if reading_is_new:
            self.rssi_filtered = self.rssi_kalman.update_adaptive(adjusted_rssi, ref_power, timestamp=timestamp)
        elif self.rssi_kalman.is_initialized:
            self.rssi_filtered = self.rssi_kalman.estimate

        # Calculate raw distance from unfiltered RSSI (for comparison/diagnostics)
        distance = rssi_to_metres(adjusted_rssi, ref_power, self.conf_attenuation)
        self.rssi_distance_raw = distance

        # Warn if signal is unrealistically strong compared to ref_power.
        # This suggests calibration may be needed. At 0cm distance, RSSI should still
        # be somewhat below ref_power due to near-field effects and antenna coupling.
        # If RSSI exceeds ref_power by more than ~25 dB, the ref_power is likely wrong.
        # Only warn if the suggested ref_power differs by at least 5 dB from current value.
        rssi_headroom = adjusted_rssi - ref_power
        if rssi_headroom >= 30:
            _LOGGER_SPAM_LESS.warning(
                f"calibration_warning_{self.device_address}",
                "Device %s has RSSI %.0f dBm which is %.0f dB stronger than ref_power %.0f dBm (from %s). "
                "This results in minimum distance (%.1fm). Consider calibrating ref_power for this device. "
                "Suggested ref_power: %.0f dBm (set 1m from a scanner, or use this RSSI + ~25 dB headroom)",
                self._device.name,
                adjusted_rssi,
                rssi_headroom,
                ref_power,
                ref_power_source,
                distance,
                adjusted_rssi - 25,  # Suggest a ref_power that would give ~1m distance at this signal strength
            )

        if reading_is_new:
            # Add a new historical reading
            self.hist_distance.insert(0, distance)
            # don't insert into hist_distance_by_interval, that's done by the caller.
        elif self.rssi_distance is not None:
            # We are over-riding readings between cycles.
            # We will force the new measurement, but only if we were
            # already showing a "current" distance, as we don't want
            # to "freshen" a measurement that was already out of date,
            # hence the elif not none above.
            self.rssi_distance = distance
            if len(self.hist_distance) > 0:
                self.hist_distance[0] = distance
            else:
                self.hist_distance.append(distance)
            if len(self.hist_distance_by_interval) > 0:
                self.hist_distance_by_interval[0] = distance
            # We don't else because we don't want to *add* a hist-by-interval reading, only
            # modify in-place.
        return distance

    def set_ref_power(self, value: float) -> float | None:
        """
        Set a new reference power and return the resulting distance.

        Typically called from the parent device when either the user changes the calibration
        of ref_power for a device, or when a metadevice takes on a new source device, and
        propagates its own ref_power to our parent.

        Note that it is unlikely to return None as its only returning the raw, not filtered
        distance = the exception being uninitialised entries.
        """
        # When the user updates the ref_power we want to reflect that change immediately,
        # and not subject it to the normal smoothing algo.
        # But make sure it's actually different, in case it's just a metadevice propagating
        # its own ref_power without need.
        if value != self.ref_power:
            self.ref_power = value
            # Reset Kalman filter and distance history to avoid using stale values
            # calculated with old ref_power (would cause incorrect distance calculations)
            self.rssi_kalman.reset()
            self.rssi_filtered = None
            self.hist_distance.clear()
            self.hist_distance_by_interval.clear()
            # Clear related parallel history arrays to maintain sync (hist_stamp and
            # hist_distance must stay in sync for velocity calculations)
            self.hist_stamp.clear()
            self.hist_rssi.clear()
            self.hist_interval.clear()
            self.hist_velocity.clear()
            return self._update_raw_distance(reading_is_new=False)
        return self.rssi_distance_raw

    def _clear_stale_history(self) -> None:
        """
        Clear distance and RSSI history when advert is stale.

        Also clears hist_distance to maintain synchronization between the two
        distance history lists. Previously only hist_distance_by_interval was
        cleared, which could cause desynchronization.
        """
        self.rssi_distance = None
        self.rssi_filtered = None
        self.rssi_kalman.reset()  # Reset Kalman filter state for fresh start
        if len(self.hist_distance_by_interval) > 0:
            self.hist_distance_by_interval.clear()
        if len(self.hist_rssi_by_interval) > 0:
            self.hist_rssi_by_interval.clear()
        # Bug Fix: Also clear hist_distance to maintain sync with hist_distance_by_interval
        # This prevents velocity calculations from using stale data when area selection
        # uses fresh (empty) hist_distance_by_interval data.
        if len(self.hist_distance) > 0:
            self.hist_distance.clear()
        if len(self.hist_stamp) > 0:
            self.hist_stamp.clear()
        if len(self.hist_velocity) > 0:
            self.hist_velocity.clear()

    def _compute_smoothed_distance(self) -> float:
        """
        Compute smoothed distance using Kalman-filtered RSSI and median fallback.

        Returns the best estimate of current distance combining:
        1. Kalman-filtered RSSI converted to distance (primary)
        2. Median of historical distances (fallback)
        """
        # Primary: Use Kalman-filtered RSSI for distance calculation
        if self.rssi_filtered is not None:
            ref_power, _ = self._get_effective_ref_power()
            return rssi_to_metres(self.rssi_filtered, ref_power, self.conf_attenuation)

        # Fallback: Calculate median of distance history
        if len(self.hist_distance_by_interval) > 0:
            sorted_distances = sorted(d for d in self.hist_distance_by_interval if d is not None)
            if sorted_distances:
                n = len(sorted_distances)
                mid = n // 2
                if n % 2 == 0:
                    return (sorted_distances[mid - 1] + sorted_distances[mid]) / 2
                return sorted_distances[mid]

        return self.rssi_distance_raw or DISTANCE_INFINITE

    def calculate_data(self) -> None:
        """
        Filter and update distance estimates.

        The smoothing pipeline accepts new distances immediately, clears stale
        values after 60s without updates to avoid zombie readings, and applies a
        velocity guard before computing a moving minimum/average blend across the
        retained history window.
        """
        new_stamp = self.new_stamp
        self.new_stamp = None

        if self.rssi_distance is None and new_stamp is not None:
            self.rssi_distance = self.rssi_distance_raw
            if self.rssi_distance_raw is not None:
                # Bug Fix: When initializing hist_distance_by_interval, also ensure
                # hist_distance is synchronized. This prevents the two lists from
                # having inconsistent data when one is cleared but the other isn't.
                self.hist_distance_by_interval.clear()
                self.hist_distance_by_interval.append(self.rssi_distance_raw)
                # Sync hist_distance if it's empty or has stale data
                if len(self.hist_distance) == 0:
                    self.hist_distance.append(self.rssi_distance_raw)

        # ADAPTIVE TIMEOUT: Use device's MAXIMUM observed advertisement interval to determine staleness.
        # Using MAX instead of AVG ensures we don't mark devices as stale during deep sleep cycles.
        # Smartphones can have intervals ranging from 1-10s (active) to 30-360s (deep sleep).
        elif new_stamp is None:
            if len(self.hist_stamp) >= 2:
                # Calculate intervals between consecutive timestamps
                intervals = [
                    self.hist_stamp[i] - self.hist_stamp[i + 1]
                    for i in range(min(10, len(self.hist_stamp) - 1))
                    if self.hist_stamp[i + 1] is not None
                ]
                if intervals:
                    max_interval = max(intervals)
                    # Use 2x maximum interval, clamped between DEFAULT (60s) and LIMIT (360s)
                    # Using MAX (not AVG) ensures deep sleep intervals are respected
                    self.adaptive_timeout = max(AREA_MAX_AD_AGE_DEFAULT, min(AREA_MAX_AD_AGE_LIMIT, max_interval * 2))

            if self.stamp is None or self.stamp < monotonic_time_coarse() - self.adaptive_timeout:
                self._clear_stale_history()

        else:
            if len(self.hist_stamp) > 1 and len(self.hist_distance) > 1:
                velo_newdistance = self.hist_distance[0]
                velo_newstamp = self.hist_stamp[0]
                peak_velocity = 0.0
                delta_t = velo_newstamp - self.hist_stamp[1]
                delta_d = velo_newdistance - self.hist_distance[1]
                if delta_t > 0:
                    peak_velocity = delta_d / delta_t
                # Check ALL historical readings to find the peak absolute velocity.
                # This catches both rapid approaches (negative velocity) and rapid departures
                # (positive velocity). Previously only positive velocities were checked,
                # which allowed devices to "jump closer" at impossible speeds.
                for old_distance, old_stamp in zip(self.hist_distance[2:], self.hist_stamp[2:], strict=False):
                    if old_stamp is None:
                        continue
                    delta_t = velo_newstamp - old_stamp
                    if delta_t <= 0:
                        continue
                    delta_d = velo_newdistance - old_distance
                    velocity = delta_d / delta_t
                    # Track the highest absolute velocity (catches both directions)
                    if abs(velocity) > abs(peak_velocity):  # noqa: RUF100, PLR1730
                        peak_velocity = velocity
                velocity = peak_velocity
            else:
                velocity = 0.0

            self.hist_velocity.insert(0, velocity)

            # Use absolute velocity to catch impossible speeds in BOTH directions.
            # A device jumping from 10m to 1m in 1 second (-9 m/s) is just as
            # impossible as jumping from 1m to 10m (+9 m/s).
            max_velocity = self.conf_max_velocity if self.conf_max_velocity is not None else DEFAULT_MAX_VELOCITY

            # FIX: Dynamic noise threshold based on user's max_velocity config.
            # This adapts to different use cases:
            # - Default walking (3 m/s): noise > 9 m/s
            # - Vehicle tracking (20 m/s): noise > 60 m/s
            # See VELOCITY_NOISE_MULTIPLIER in const.py for detailed documentation.
            noise_velocity_threshold = max_velocity * VELOCITY_NOISE_MULTIPLIER
            abs_velocity = abs(velocity)

            if abs_velocity > noise_velocity_threshold:
                # IMPOSSIBLE SPIKE: BLE noise caused a measurement error.
                # Completely ignore this reading - don't count it as a block.
                # This prevents noise from triggering false teleport recovery.
                if self._device.create_sensor:
                    _LOGGER.debug(
                        "BLE noise spike for %s (%.1fm/s > %.1fm/s threshold), ignoring as measurement error",
                        self._device.name,
                        abs_velocity,
                        noise_velocity_threshold,
                    )
                # Use previous distance value instead
                if len(self.hist_distance_by_interval) > 0:
                    self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
                else:
                    self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
                # Don't modify velocity_blocked_count - noise should not trigger recovery

            elif abs_velocity > max_velocity:
                # PLAUSIBLE FAST: Device might have actually teleported (moved quickly).
                # This is the "velocity trap" scenario - count toward recovery.
                self.velocity_blocked_count += 1

                # Check if we've hit the teleport recovery threshold
                if self.velocity_blocked_count >= VELOCITY_TELEPORT_THRESHOLD:
                    # Device has been consistently measured at a "teleported" position.
                    # Accept the new position and reset history to break the trap.
                    _LOGGER.info(
                        "Teleport recovery for %s: accepting new position after %d "
                        "consecutive velocity blocks (velocity=%.1fm/s)",
                        self._device.name,
                        self.velocity_blocked_count,
                        velocity,
                    )
                    # Accept the new measurement
                    self.hist_distance_by_interval.clear()
                    self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
                    # Clear the position history so the new position becomes baseline
                    self.hist_distance.clear()
                    self.hist_distance.insert(0, self.rssi_distance_raw)
                    self.hist_stamp.clear()
                    self.hist_stamp.insert(0, self.stamp)
                    self.hist_velocity.clear()
                    # Reset the counter
                    self.velocity_blocked_count = 0
                else:
                    # Still blocking - use previous distance
                    if self._device.create_sensor:
                        _LOGGER.debug(
                            "This sparrow %s flies too fast (%.1fm/s), ignoring (block %d/%d)",
                            self._device.name,
                            velocity,
                            self.velocity_blocked_count,
                            VELOCITY_TELEPORT_THRESHOLD,
                        )
                    if len(self.hist_distance_by_interval) > 0:
                        self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
                    else:
                        self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
            else:
                # Velocity is acceptable - accept the measurement and reset counter
                self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
                # FIX: Teleport Recovery - Reset counter when velocity is normal
                self.velocity_blocked_count = 0

            smoothing_samples = (
                self.conf_smoothing_samples if self.conf_smoothing_samples is not None else DEFAULT_SMOOTHING_SAMPLES
            )
            if len(self.hist_distance_by_interval) > smoothing_samples:
                del self.hist_distance_by_interval[smoothing_samples:]

            # Update raw RSSI history for physical proximity checks
            if self.rssi is not None:
                self.hist_rssi_by_interval.insert(0, self.rssi)
                if len(self.hist_rssi_by_interval) > RSSI_HISTORY_SAMPLES:
                    del self.hist_rssi_by_interval[RSSI_HISTORY_SAMPLES:]

            # Calculate smoothed distance using Kalman-filtered RSSI (scientific best practice)
            smoothed_dist = self._compute_smoothed_distance()

            # ALWAYS use the Kalman-filtered distance. The previous logic bypassed the
            # filter when raw distance was shorter ("quick response when approaching"),
            # but this allowed single-sample spikes to pass through unfiltered.
            # The Kalman filter already adapts its gain based on measurement patterns -
            # consistent approaches will be tracked, while spikes will be attenuated.
            # The velocity guard (above) provides additional protection against
            # physically impossible movements in either direction.
            self.rssi_distance = smoothed_dist

        del self.hist_distance[HIST_KEEP_COUNT:]
        del self.hist_interval[HIST_KEEP_COUNT:]
        del self.hist_rssi[HIST_KEEP_COUNT:]
        del self.hist_stamp[HIST_KEEP_COUNT:]
        del self.hist_velocity[HIST_KEEP_COUNT:]

    def to_dict(self) -> dict[str, Any]:
        """Convert class to serialisable dict for dump_devices."""
        # using "is" comparisons instead of string matching means
        # linting and typing can catch errors.
        out = {}
        for var, val in vars(self).items():
            if val in [self.options]:
                # skip certain vars that we don't want in the dump output.
                continue
            if val in [self.options, self._device, self.scanner_device]:
                # objects we might want to represent but not fully iterate etc.
                out[var] = val.__repr__()
                continue
            if val is self.local_name:
                out[var] = {}
                for namestr, namebytes in self.local_name:
                    out[var][namestr] = namebytes.hex()
                continue
            if val is self.manufacturer_data:
                out[var] = {}
                for manrow in self.manufacturer_data:
                    for manid, manbytes in manrow.items():
                        out[var][manid] = manbytes.hex()
                continue
            if val is self.service_data:
                out[var] = {}
                for svrow in self.service_data:
                    for svid, svbytes in svrow.items():
                        out[var][svid] = svbytes.hex()
                continue
            if isinstance(val, str | int):
                out[var] = val
                continue
            if isinstance(val, float):
                out[var] = round(val, 4)
                continue
            if isinstance(val, list):
                out[var] = []
                for row in val:
                    if isinstance(row, float):
                        out[var].append(round(row, 4))
                    else:
                        out[var].append(row)
                continue
            out[var] = val.__repr__()
        return out

    def __repr__(self) -> str:
        """Help debugging by giving it a clear name instead of empty dict."""
        return f"{self.device_address}__{self.scanner_device.name}"

    def median_rssi(self) -> float | None:
        """
        Return the median of recent raw RSSI values.

        Uses hist_rssi_by_interval for robust signal strength comparison.
        Median is more robust against outliers than mean.
        Returns None if no history is available.
        """
        if not self.hist_rssi_by_interval:
            # Fall back to current RSSI if no history yet
            return self.rssi
        sorted_rssi = sorted(self.hist_rssi_by_interval)
        n = len(sorted_rssi)
        mid = n // 2
        if n % 2 == 0:
            return (sorted_rssi[mid - 1] + sorted_rssi[mid]) / 2
        return sorted_rssi[mid]

    def _get_effective_rssi_variance(self, nowstamp: float | None = None) -> float:
        """
        Get effective RSSI variance with edge-case handling.

        Applies floors for cold start and converged states, and inflates
        variance based on measurement staleness.

        Args:
            nowstamp: Current timestamp for staleness calculation.
                      If None, no staleness inflation is applied.

        Returns:
            Effective RSSI variance in dBm².

        """
        # Edge case: Kalman filter not initialized
        if not self.rssi_kalman.is_initialized:
            return VARIANCE_FALLBACK_UNINIT

        variance = self.rssi_kalman.variance

        # Apply floor based on sample count (cold start vs converged)
        if self.rssi_kalman.sample_count < VARIANCE_COLD_START_SAMPLES:
            # Cold start: use higher floor to prevent premature decisions
            variance = max(variance, VARIANCE_FLOOR_COLD_START)
        else:
            # Converged: use standard floor to prevent over-confidence
            variance = max(variance, VARIANCE_FLOOR_CONVERGED)

        # Time-based variance inflation for stale measurements
        # Uses public property to avoid accessing private member
        if nowstamp is not None:
            last_update = self.rssi_kalman.last_update_time
            if last_update is not None:
                staleness = nowstamp - last_update
                if staleness > 0:
                    # Inflate variance by process_noise * staleness
                    variance += self.rssi_kalman.process_noise * staleness

        return variance

    def get_distance_variance(self, nowstamp: float | None = None) -> float:
        """
        Calculate distance variance using Gaussian Error Propagation.

        Converts RSSI variance (dBm^2) to distance variance (m^2) using the
        derivative of the log-distance path loss model.

        Mathematical derivation:
            RSSI = ref_power - 10 * n * log10(d)
            d = 10^((ref_power - RSSI) / (10 * n))

            dd/dRSSI = -d * ln(10) / (10 * n)

            var_d = (dd/dRSSI)^2 * var_RSSI
                  = (d * ln(10) / (10 * n))^2 * var_RSSI

        Handles edge cases:
            - Cold start (high RSSI variance floor)
            - Converged filter (RSSI variance floor)
            - Stale measurements (time-inflated RSSI variance)
            - Uninitialized filter (fallback RSSI variance)
            - Near-field (fixed distance variance)
            - Far-field (capped distance variance)

        Args:
            nowstamp: Current timestamp for staleness calculation.

        Returns:
            Distance variance in m^2.

        """
        # 1. Get effective RSSI variance with all floors and inflation
        rssi_variance = self._get_effective_rssi_variance(nowstamp)

        # 2. Get current distance estimate
        distance = self.rssi_distance
        if distance is None:
            distance = self.rssi_distance_raw
        if distance is None or distance <= 0:
            distance = 1.0  # Fallback to 1m if no distance available

        # 3. Handle near-field: use fixed variance to avoid instability
        if distance < MIN_DISTANCE_FOR_VARIANCE:
            return NEAR_FIELD_DISTANCE_VARIANCE

        # 4. Get path loss exponent using TWO-SLOPE MODEL (matches rssi_to_metres)
        # P2 fix: Near-field uses PATH_LOSS_EXPONENT_NEAR (~1.8), not configured attenuation
        # This is critical because the derivative dd/dRSSI depends on the exponent used
        # for distance calculation, not just the configured far-field attenuation.
        # P3 fix: Use <= to match rssi_to_metres boundary (util.py:204)
        if distance <= TWO_SLOPE_BREAKPOINT_METRES:
            # Near-field: use scientifically-derived exponent (matches rssi_to_metres)
            path_loss_exponent = PATH_LOSS_EXPONENT_NEAR
        else:
            # Far-field: use user-configured attenuation
            path_loss_exponent = self.conf_attenuation
            if path_loss_exponent is None or path_loss_exponent <= 0:
                path_loss_exponent = DEFAULT_ATTENUATION

        # 5. Calculate variance using correct error propagation
        # CORRECTED FORMULA (peer review): ln(10) is in the NUMERATOR
        # factor = dd/dRSSI = d * ln(10) / (10 * n)
        factor = (distance * math.log(10)) / (10.0 * path_loss_exponent)

        # var_d = factor^2 * var_RSSI
        distance_variance = (factor**2) * rssi_variance

        # 6. Cap far-field variance to prevent unrealistic values
        return min(distance_variance, MAX_DISTANCE_VARIANCE)
