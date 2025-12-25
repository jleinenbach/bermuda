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

from typing import TYPE_CHECKING, Final

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    _LOGGER,
    CONF_ATTENUATION,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    DISTANCE_INFINITE,
    HIST_KEEP_COUNT,
)

# from .const import _LOGGER_SPAM_LESS
from .util import clean_charbuf, rssi_to_metres

if TYPE_CHECKING:
    from bleak.backends.scanner import AdvertisementData

    from .bermuda_device import BermudaDevice

# The if instead of min/max triggers PLR1730, but when
# split over two lines, ruff removes it, then complains again.
# so we're just disabling it for the whole file.
# https://github.com/astral-sh/ruff/issues/4244
# ruff: noqa: PLR1730


class BermudaAdvert(dict):
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

    def __hash__(self) -> int:
        """The device-mac / scanner mac uniquely identifies a received advertisement pair."""
        return hash((self.device_address, self.scanner_address))

    def __init__(
        self,
        parent_device: BermudaDevice,  # The device being tracked
        advertisementdata: AdvertisementData,  # The advertisement info from the device, received by the scanner
        options,
        scanner_device: BermudaDevice,  # The scanner device that "saw" it.
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
        self.hist_stamp: list[float] = []
        self.hist_rssi: list[int] = []
        self.hist_distance: list[float] = []
        self.hist_distance_by_interval: list[float] = []  # updated per-interval
        self.hist_interval = []  # WARNING: This is actually "age of ad when we polled"
        self.hist_velocity: list[float] = []  # Effective velocity versus previous stamped reading
        self.conf_rssi_offset = self.options.get(CONF_RSSI_OFFSETS, {}).get(self.scanner_address, 0)
        self.conf_ref_power = self.options.get(CONF_REF_POWER)
        self.conf_attenuation = self.options.get(CONF_ATTENUATION)
        self.conf_max_velocity = self.options.get(CONF_MAX_VELOCITY)
        self.conf_smoothing_samples = self.options.get(CONF_SMOOTHING_SAMPLES)
        self.local_name: list[tuple[str, bytes]] = []
        self.manufacturer_data: list[dict[int, bytes]] = []
        self.service_data: list[dict[str, bytes]] = []
        self.service_uuids: list[str] = []

        # Just pass the rest on to update...
        self.update_advertisement(advertisementdata, self.scanner_device)

    def apply_new_scanner(self, scanner_device: BermudaDevice):
        self.name: str = scanner_device.name  # or scandata.scanner.name
        self.scanner_device = scanner_device  # links to the source device
        if self.scanner_address != scanner_device.address:
            _LOGGER.error("Advert %s received new scanner with wrong address %s", self.__repr__(), scanner_device)
        self.area_id: str | None = scanner_device.area_id
        self.area_name: str | None = scanner_device.area_name
        # Only remote scanners log timestamps, local usb adaptors do not.
        self.scanner_sends_stamps = scanner_device.is_remote_scanner

    def update_advertisement(self, advertisementdata: AdvertisementData, scanner_device: BermudaDevice):
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

        if self.scanner_device.area_id != self.area_id:
            self.area_id = self.scanner_device.area_id
            self.area_name = self.scanner_device.area_name

        if self.scanner_device.area_id is None:
            now = monotonic_time_coarse()
            last_check = getattr(self.scanner_device, "last_devreg_check", 0.0)
            if now - last_check > 60:
                self.scanner_device.async_as_scanner_resolve_device_entries()
                self.scanner_device.last_devreg_check = now
                if self.scanner_device.area_id is not None:
                    self.area_id = self.scanner_device.area_id
                    self.area_name = self.scanner_device.area_name

        scanner = self.scanner_device
        new_stamp: float | None = None

        if self.scanner_sends_stamps:
            new_stamp = scanner.async_as_scanner_get_stamp(self.device_address)
            if new_stamp is None:
                self.stale_update_count += 1
                return
            if self.stamp > new_stamp:
                self.stale_update_count += 1
                _LOGGER.debug("Advert from %s for %s is OLDER than last recorded", scanner.name, self._device.name)
                return
            if self.stamp == new_stamp:
                self.stale_update_count += 1
                return
        elif self.rssi != advertisementdata.rssi:
            new_stamp = monotonic_time_coarse() - 3.0
        else:
            return

        if new_stamp > self.scanner_device.last_seen + 0.01:
            self.scanner_device.last_seen = new_stamp

        if len(self.hist_stamp) == 0 or new_stamp is not None:
            self.rssi = advertisementdata.rssi
            self.hist_rssi.insert(0, self.rssi)

            self._update_raw_distance(reading_is_new=True)

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
            if advertisementdata.service_data not in self.manufacturer_data[1:]:
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

    def _update_raw_distance(self, reading_is_new=True) -> float:
        """
        Converts rssi to raw distance and updates history stack and
        returns the new raw distance.

        reading_is_new should only be called by the regular update
        cycle, as it creates a new entry in the histories. Call with
        false if you just need to set / override distance measurements
        immediately, perhaps between cycles, in order to reflect a
        setting change (such as altering a device's ref_power setting).
        """
        # Check if we should use a device-based ref_power
        if self.ref_power == 0:  # No user-supplied per-device value
            # use global default
            ref_power = self.conf_ref_power
        else:
            ref_power = self.ref_power

        distance = rssi_to_metres(self.rssi + self.conf_rssi_offset, ref_power, self.conf_attenuation)
        self.rssi_distance_raw = distance
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
            return self._update_raw_distance(False)
        return self.rssi_distance_raw

    def calculate_data(self):
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
                self.hist_distance_by_interval.clear()
                self.hist_distance_by_interval.append(self.rssi_distance_raw)

        # ADJUSTED TIMEOUT (60s)
        # Prevents flickering but avoids zombie devices (stuck in wrong area >1min).
        elif new_stamp is None and (self.stamp is None or self.stamp < monotonic_time_coarse() - 60):
            self.rssi_distance = None
            if len(self.hist_distance_by_interval) > 0:
                self.hist_distance_by_interval.clear()

        else:
            if len(self.hist_stamp) > 1:
                velo_newdistance = self.hist_distance[0]
                velo_newstamp = self.hist_stamp[0]
                peak_velocity = 0
                delta_t = velo_newstamp - self.hist_stamp[1]
                delta_d = velo_newdistance - self.hist_distance[1]
                if delta_t > 0:
                    peak_velocity = delta_d / delta_t
                if peak_velocity >= 0:
                    for old_distance, old_stamp in zip(self.hist_distance[2:], self.hist_stamp[2:], strict=False):
                        if old_stamp is None:
                            continue
                        delta_t = velo_newstamp - old_stamp
                        if delta_t <= 0:
                            continue
                        delta_d = velo_newdistance - old_distance
                        velocity = delta_d / delta_t
                        if velocity > peak_velocity:  # noqa: RUF100, PLR1730
                            peak_velocity = velocity
                velocity = peak_velocity
            else:
                velocity = 0

            self.hist_velocity.insert(0, velocity)

            if velocity > self.conf_max_velocity:
                if self._device.create_sensor:
                    _LOGGER.debug(
                        "This sparrow %s flies too fast (%2fm/s), ignoring",
                        self._device.name,
                        velocity,
                    )
                if len(self.hist_distance_by_interval) > 0:
                    self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
                else:
                    self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
            else:
                self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)

            if len(self.hist_distance_by_interval) > self.conf_smoothing_samples:
                del self.hist_distance_by_interval[self.conf_smoothing_samples :]

            dist_total: float = 0
            local_min: float = self.rssi_distance_raw or DISTANCE_INFINITE
            for distance in self.hist_distance_by_interval:
                if distance is not None and distance <= local_min:
                    local_min = distance
                dist_total += local_min

            if (_hist_dist_len := len(self.hist_distance_by_interval)) > 0:
                movavg = dist_total / _hist_dist_len
            else:
                movavg = local_min

            if self.rssi_distance_raw is None or movavg < self.rssi_distance_raw:
                self.rssi_distance = movavg
            else:
                self.rssi_distance = self.rssi_distance_raw

        del self.hist_distance[HIST_KEEP_COUNT:]
        del self.hist_interval[HIST_KEEP_COUNT:]
        del self.hist_rssi[HIST_KEEP_COUNT:]
        del self.hist_stamp[HIST_KEEP_COUNT:]
        del self.hist_velocity[HIST_KEEP_COUNT:]

    def to_dict(self):
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
