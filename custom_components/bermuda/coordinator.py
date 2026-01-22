"""DataUpdateCoordinator for Bermuda bluetooth data."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import aiofiles
import voluptuous as vol
import yaml
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.api import _get_manager
from homeassistant.const import MAJOR_VERSION as HA_VERSION_MAJ
from homeassistant.const import MINOR_VERSION as HA_VERSION_MIN
from homeassistant.const import Platform
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    floor_registry as fr,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import (
    EVENT_DEVICE_REGISTRY_UPDATED,
    EventDeviceRegistryUpdatedData,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.dt import get_age, now

from .bermuda_device import BermudaDevice
from .bermuda_irk import BermudaIrkManager
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    AREA_MAX_AD_AGE_DEFAULT,
    AREA_MAX_AD_AGE_LIMIT,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_FMDN_EID_FORMAT,
    CONF_FMDN_MODE,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    CONF_USE_PHYSICAL_RSSI_PRIORITY,
    CONF_USE_UKF_AREA_SELECTION,
    CROSS_FLOOR_MIN_HISTORY,
    CROSS_FLOOR_STREAK,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USE_PHYSICAL_RSSI_PRIORITY,
    DEFAULT_USE_UKF_AREA_SELECTION,
    DOMAIN,
    DOMAIN_GOOGLEFINDMY,
    DOMAIN_PRIVATE_BLE_DEVICE,
    EVIDENCE_WINDOW_SECONDS,
    INCUMBENT_MARGIN_METERS,
    MARGIN_MOVING_PERCENT,
    MARGIN_SETTLING_PERCENT,
    MARGIN_STATIONARY_METERS,
    MARGIN_STATIONARY_PERCENT,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
    MOVEMENT_STATE_MOVING,
    MOVEMENT_STATE_SETTLING,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_DEFAULT,
    PRUNE_TIME_FMDN,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_KNOWN_IRK,
    PRUNE_TIME_REDACTIONS,
    PRUNE_TIME_UNKNOWN_IRK,
    REPAIR_SCANNER_WITHOUT_AREA,
    RSSI_CONSISTENCY_MARGIN_DB,
    SAME_FLOOR_MIN_HISTORY,
    SAME_FLOOR_STREAK,
    SAVEOUT_COOLDOWN,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
    UKF_LOW_CONFIDENCE_THRESHOLD,
    UKF_MIN_MATCH_SCORE,
    UKF_MIN_SCANNERS,
    UKF_RETENTION_THRESHOLD,
    UKF_RSSI_SANITY_MARGIN,
    UKF_STICKINESS_BONUS,
    UKF_WEAK_SCANNER_MIN_DISTANCE,
    UPDATE_INTERVAL,
    VIRTUAL_DISTANCE_MIN_SCORE,
    VIRTUAL_DISTANCE_SCALE,
)
from .correlation import AreaProfile, CorrelationStore, RoomProfile, z_scores_to_confidence
from .filters import UnscentedKalmanFilter
from .fmdn import FmdnIntegration
from .scanner_calibration import ScannerCalibrationManager, update_scanner_calibration
from .util import is_mac_address, mac_explode_formats, normalize_address, normalize_mac

Cancellable = Callable[[], None]
DUMP_DEVICE_SOFT_LIMIT = 1200
CORRELATION_SAVE_INTERVAL = 300  # Save learned correlations every 5 minutes


if TYPE_CHECKING:
    from habluetooth import BaseHaScanner, BluetoothServiceInfoBleak
    from homeassistant.components.bluetooth import (
        BluetoothChange,
    )
    from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager

    from . import BermudaConfigEntry
    from .bermuda_advert import BermudaAdvert

# Using "if" instead of "min/max" triggers PLR1730, but when
# split over two lines, ruff removes it, then complains again.
# so we're just disabling it for the whole file.
# https://github.com/astral-sh/ruff/issues/4244
# ruff: noqa: PLR1730


class BermudaDataUpdateCoordinator(DataUpdateCoordinator[Any]):
    """
    Class to manage fetching data from the Bluetooth component.

    Since we are not actually using an external API and only computing local
    data already gathered by the bluetooth integration, the update process is
    very cheap, and the processing process (currently) rather cheap.

    TODO / IDEAS:
    - when we get to establishing a fix, we can apply a path-loss factor to
      a calculated vector based on previously measured losses on that path.
      We could perhaps also fine-tune that with real-time measurements from
      fixed beacons to compensate for environmental factors.
    - An "obstruction map" or "radio map" could provide field strength estimates
      at given locations, and/or hint at attenuation by counting "wall crossings"
      for a given vector/path.

    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BermudaConfigEntry,
    ) -> None:
        """Initialize."""
        self.platforms: list[Platform] = []
        self.config_entry = entry

        self.sensor_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        # set some version flags
        # Cast to int to avoid mypy literal comparison warnings when HA version is known at import time
        _ha_maj = int(HA_VERSION_MAJ)
        _ha_min = int(HA_VERSION_MIN)
        self.hass_version_min_2025_2 = _ha_maj > 2025 or (_ha_maj == 2025 and _ha_min >= 2)
        # when habasescanner.discovered_device_timestamps became a public method.
        self.hass_version_min_2025_4 = _ha_maj > 2025 or (_ha_maj == 2025 and _ha_min >= 4)

        # ##### Redaction Data ###
        #
        # match/replacement pairs for redacting addresses
        self.redactions: dict[str, str] = {}
        # Any remaining MAC addresses will be replaced with this. We define it here
        # so we can compile it once. MAC addresses may have [:_-] separators.
        self._redact_generic_re = re.compile(
            r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})"
        )
        self._redact_generic_sub = r"\g<start>:xx:xx:xx:xx:\g<end>"

        self.stamp_redactions_expiry: float | None = None

        self.update_in_progress: bool = False  # A lock to guard against huge backlogs / slow processing
        self.stamp_last_update: float = 0  # Last time we ran an update, from monotonic_time_coarse()
        self.stamp_last_update_started: float = 0
        self.stamp_last_prune: float = 0  # When we last pruned device list

        self.member_uuids: dict[int, str] = {}
        self.company_uuids: dict[int, str] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

        self._waitingfor_load_manufacturer_ids = True
        entry.async_create_background_task(
            hass, self.async_load_manufacturer_ids(), "Load Bluetooth IDs", eager_start=True
        )

        self._manager: HomeAssistantBluetoothManager = _get_manager(hass)  # instance of the bluetooth manager
        self._hascanners: set[BaseHaScanner] = set()  # Links to the backend scanners
        self._hascanner_timestamps: dict[str, dict[str, float]] = {}  # scanner_address, device_address, stamp
        self._scanner_list: set[str] = set()
        self._scanners: set[BermudaDevice] = set()  # Set of all in self.devices that is_scanner=True
        self.irk_manager = BermudaIrkManager()
        self.fmdn = FmdnIntegration(self)
        self.scanner_calibration = ScannerCalibrationManager()

        # Scanner correlation learning for improved area localization
        self.correlation_store = CorrelationStore(hass)
        self.correlations: dict[str, dict[str, AreaProfile]] = {}
        self.room_profiles: dict[str, RoomProfile] = {}  # Device-independent room fingerprints
        self._correlations_loaded = False
        self._last_correlation_save: float = 0

        # UKF (Unscented Kalman Filter) instances per device for multi-scanner fusion
        # Key: device address, Value: UKF instance tracking RSSI from all visible scanners
        self.device_ukfs: dict[str, UnscentedKalmanFilter] = {}

        self.ar = ar.async_get(self.hass)
        self.er = er.async_get(self.hass)
        self.dr = dr.async_get(self.hass)
        self.fr = fr.async_get(self.hass)
        self.have_floors: bool = self.init_floors()

        self._scanners_without_areas: list[str] | None = None  # Tracks any proxies that don't have an area assigned.

        # Track the list of Private BLE devices, noting their entity id
        # and current "last address".
        self.pb_state_sources: dict[str, str | None] = {}

        self.metadevices: dict[str, BermudaDevice] = {}

        self._ad_listener_cancel: Cancellable | None = None

        # Tracks the last stamp that we *actually* saved our config entry. Mostly for debugging,
        # we use a request stamp for tracking our add_job request.
        self.last_config_entry_update: float = 0  # Stamp of last *save-out* of config.data

        # We want to delay the first save-out, since it takes a few seconds for things
        # to stabilise. So set the stamp into the future.
        self.last_config_entry_update_request = (
            monotonic_time_coarse() + SAVEOUT_COOLDOWN
        )  # Stamp for save-out requests

        # AJG 2025-04-23 Disabling, see the commented method below for notes.
        # self.config_entry.async_on_unload(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self.handle_state_changes))

        # First time around we freshen the restored scanner info by
        # forcing a scan of the captured info.
        self._scanner_init_pending = True

        self._seed_configured_devices_done = False

        # First time go through the private ble devices to see if there's
        # any there for us to track.
        self._do_private_device_init = True

        # First time go through the googlefindmy (FMDN) devices to see if there's
        # any there for us to track.
        self._do_fmdn_device_init = True

        # Listen for changes to the device registry and handle them.
        # Primarily for changes to scanners and Private BLE Devices.
        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, self.handle_devreg_changes)
        )

        self.options: dict[str, Any] = {}

        # TODO: This is only here because we haven't set up migration of config
        # entries yet, so some users might not have this defined after an update.
        self.options[CONF_ATTENUATION] = DEFAULT_ATTENUATION
        self.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        self.options[CONF_MAX_RADIUS] = DEFAULT_MAX_RADIUS
        self.options[CONF_MAX_VELOCITY] = DEFAULT_MAX_VELOCITY
        self.options[CONF_REF_POWER] = DEFAULT_REF_POWER
        self.options[CONF_SMOOTHING_SAMPLES] = DEFAULT_SMOOTHING_SAMPLES
        self.options[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL
        self.options[CONF_RSSI_OFFSETS] = {}
        self.options[CONF_USE_UKF_AREA_SELECTION] = DEFAULT_USE_UKF_AREA_SELECTION

        if hasattr(entry, "options"):
            # Firstly, on some calls (specifically during reload after settings changes)
            # we seem to get called with a non-existant config_entry.
            # Anyway... if we DO have one, convert it to a plain dict so we can
            # serialise it properly when it goes into the device and scanner classes.
            for key, val in entry.options.items():
                if key in (
                    CONF_ATTENUATION,
                    CONF_DEVICES,
                    CONF_DEVTRACK_TIMEOUT,
                    CONF_FMDN_EID_FORMAT,
                    CONF_FMDN_MODE,
                    CONF_MAX_RADIUS,
                    CONF_MAX_VELOCITY,
                    CONF_REF_POWER,
                    CONF_SMOOTHING_SAMPLES,
                    CONF_RSSI_OFFSETS,
                    CONF_USE_UKF_AREA_SELECTION,
                ):
                    self.options[key] = val

        self.devices: dict[str, BermudaDevice] = {}
        # self.updaters: dict[str, BermudaPBDUCoordinator] = {}

        # Register the dump_devices service
        hass.services.async_register(
            DOMAIN,
            "dump_devices",
            self.service_dump_devices,
            vol.Schema(
                {
                    vol.Optional("addresses"): cv.string,
                    vol.Optional("configured_devices"): cv.boolean,
                    vol.Optional("redact"): cv.boolean,
                }
            ),
            SupportsResponse.ONLY,
        )

        # Register for newly discovered / changed BLE devices
        if self.config_entry is not None:
            self.config_entry.async_on_unload(
                bluetooth.async_register_callback(
                    self.hass,
                    self.async_handle_advert,
                    bluetooth.BluetoothCallbackMatcher(connectable=False),
                    bluetooth.BluetoothScanningMode.ACTIVE,
                )
            )

    @property
    def scanner_list(self) -> set[str]:
        return self._scanner_list

    @property
    def get_scanners(self) -> set[BermudaDevice]:
        return self._scanners

    def init_floors(self) -> bool:
        """Check if the system has floors configured, and enable sensors."""
        _have_floors: bool = False
        for area in self.ar.async_list_areas():
            if area.floor_id is not None:
                _have_floors = True
                break
        _LOGGER.debug("Have_floors is %s", _have_floors)
        return _have_floors

    def scanner_list_add(self, scanner_device: BermudaDevice) -> None:
        self._scanner_list.add(scanner_device.address)
        self._scanners.add(scanner_device)
        async_dispatcher_send(self.hass, SIGNAL_SCANNERS_CHANGED)

    def scanner_list_del(self, scanner_device: BermudaDevice) -> None:
        self._scanner_list.remove(scanner_device.address)
        self._scanners.remove(scanner_device)
        async_dispatcher_send(self.hass, SIGNAL_SCANNERS_CHANGED)

    def reload_options(self) -> None:
        """
        Reload options from config entry without full restart.

        This preserves runtime state like scanner_calibration data
        while applying new user configuration.
        """
        entry = self.config_entry
        if entry is None or not hasattr(entry, "options"):
            return

        _LOGGER.debug("Reloading options without full restart")

        # Update options dict from config entry
        for key, val in entry.options.items():
            if key in (
                CONF_ATTENUATION,
                CONF_DEVICES,
                CONF_DEVTRACK_TIMEOUT,
                CONF_FMDN_EID_FORMAT,
                CONF_FMDN_MODE,
                CONF_MAX_RADIUS,
                CONF_MAX_VELOCITY,
                CONF_REF_POWER,
                CONF_SMOOTHING_SAMPLES,
                CONF_RSSI_OFFSETS,
                CONF_USE_UKF_AREA_SELECTION,
            ):
                self.options[key] = val

        # Update sensor interval if changed
        new_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        if new_interval != self.sensor_interval:
            self.sensor_interval = new_interval
            _LOGGER.info("Sensor update interval changed to %s seconds", new_interval)

        # Propagate options to existing devices
        for device in self.devices.values():
            device.options = self.options

    def get_manufacturer_from_id(self, uuid: int | str) -> tuple[str, bool] | tuple[None, None]:
        """
        An opinionated Bluetooth UUID to Name mapper.

        - uuid must be four hex chars in a string, or an `int`

        Retreives the manufacturer name from the Bluetooth SIG Member UUID listing,
        using a cached copy of https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/member_uuids.yaml

        HOWEVER: Bermuda adds some opinionated overrides for the benefit of user clarity:
        - Legal entity names may be overriden with well-known brand names
        - Special-use prefixes may be tagged as such (eg iBeacon etc)
        - Generics can be excluded by setting exclude_generics=True
        """
        if isinstance(uuid, str):
            uuid = int(uuid.replace(":", ""), 16)

        _generic = False
        # Because iBeacon and (soon) GFMD and AppleFindmy etc are common protocols, they
        # don't do a good job of uniquely identifying a manufacturer, so we use them
        # as fallbacks only.
        if uuid == 0x0BA9:
            # allterco robotics, aka...
            _name = "Shelly Devices"
        elif uuid == 0x004C:
            # Apple have *many* UUIDs, but since they don't OEM for others (AFAIK)
            # and only the iBeacon / FindMy adverts seem to be third-partied, match just
            # this one instead of their entire set.
            _name = "Apple Inc."
            _generic = True
        elif uuid == 0x181C:
            _name = "BTHome v1 cleartext"
            _generic = True
        elif uuid == 0x181E:
            _name = "BTHome v1 encrypted"
            _generic = True
        elif uuid == 0xFCD2:
            _name = "BTHome V2"  # Sponsored by Allterco / Shelly
            _generic = True
        elif uuid in self.member_uuids:
            _name = self.member_uuids[uuid]
            # Hardware manufacturers who OEM MAC PHYs etc, or offer the use
            # of their OUIs to third parties (specific known ones can be moved
            # to a case in the above conditions).
            if any(x in _name for x in ["Google", "Realtek"]):
                _generic = True
        elif uuid in self.company_uuids:
            _name = self.company_uuids[uuid]
            _generic = False
        else:
            return (None, None)
        return (_name, _generic)

    async def async_load_manufacturer_ids(self):
        """Import yaml files containing manufacturer name mappings."""
        try:
            # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/member_uuids.yaml
            file_path = self.hass.config.path(
                f"custom_components/{DOMAIN}/manufacturer_identification/member_uuids.yaml"
            )
            async with aiofiles.open(file_path) as f:
                mi_yaml = yaml.safe_load(await f.read())["uuids"]
            self.member_uuids = {member["uuid"]: member["name"] for member in mi_yaml}

            # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/company_identifiers/company_identifiers.yaml
            file_path = self.hass.config.path(
                f"custom_components/{DOMAIN}/manufacturer_identification/company_identifiers.yaml"
            )
            async with aiofiles.open(file_path) as f:
                ci_yaml = yaml.safe_load(await f.read())["company_identifiers"]
            self.company_uuids = {member["value"]: member["name"] for member in ci_yaml}
        finally:
            # Ensure that an issue reading these files (which are optional, really) doesn't stop the whole show.
            self._waitingfor_load_manufacturer_ids = False

    @callback
    def handle_devreg_changes(self, ev: Event[EventDeviceRegistryUpdatedData]) -> None:
        """
        Update our scanner list if the device registry is changed.

        This catches area changes (on scanners) and any new/changed
        Private BLE Devices.
        """
        if ev.data["action"] == "update":
            _LOGGER.debug("Device registry UPDATE. ev: %s changes: %s", ev, ev.data["changes"])
        else:
            _LOGGER.debug("Device registry has changed. ev: %s", ev)

        device_id = ev.data.get("device_id")

        if ev.data["action"] in {"create", "update"}:
            if device_id is None:
                _LOGGER.error("Received Device Registry create/update without a device_id. ev.data: %s", ev.data)
                return

            # First look for any of our devices that have a stored id on them, it'll be quicker.
            for device in self.devices.values():
                if device.entry_id == device_id:
                    # We matched, most likely a scanner.
                    if device.is_scanner:
                        self._refresh_scanners(force=True)
                        return
            # Didn't match an existing, work through the connections etc.

            # Pull up the device registry entry for the device_id
            if device_entry := self.dr.async_get(ev.data["device_id"]):
                # Work out if it's a device that interests us and respond appropriately.
                # First check identifiers for googlefindmy devices
                for ident_type, ident_id in device_entry.identifiers:
                    if ident_type == DOMAIN_GOOGLEFINDMY:
                        _LOGGER.debug("Trigger updating of FMDN Devices (googlefindmy)")
                        self._do_fmdn_device_init = True
                        break
                    if ident_type == DOMAIN:
                        # One of our sensor devices!
                        try:
                            if _device := self.devices[ident_id.lower()]:
                                _device.name_by_user = device_entry.name_by_user
                                _device.make_name()
                        except KeyError:
                            pass

                for conn_type, _conn_id in device_entry.connections:
                    if conn_type == "private_ble_device":
                        _LOGGER.debug("Trigger updating of Private BLE Devices")
                        self._do_private_device_init = True
                    elif conn_type == "ibeacon":
                        # this was probably us, nothing else to do
                        pass
                    else:
                        # might be a scanner, so let's refresh those
                        _LOGGER.debug("Trigger updating of Scanner Listings")
                        self._scanner_init_pending = True
            else:
                _LOGGER.error(
                    "Received DR update/create but device id does not exist: %s",
                    ev.data["device_id"],
                )

        elif ev.data["action"] == "remove":
            device_found = False
            for scanner in self.get_scanners:
                if scanner.entry_id == device_id:
                    _LOGGER.debug(
                        "Scanner %s removed, trigger update of scanners",
                        scanner.name,
                    )
                    self._scanner_init_pending = True
                    device_found = True
            if not device_found:
                # If we save the private ble device's device_id into devices[].entry_id
                # we could check ev.data["device_id"] against it to decide if we should
                # rescan PBLE devices. But right now we don't, so scan 'em anyway.
                _LOGGER.debug("Opportunistic trigger of update for Private BLE and FMDN Devices")
                self._do_private_device_init = True
                self._do_fmdn_device_init = True
        # The co-ordinator will only get updates if we have created entities already.
        # Since this might not always be the case (say, private_ble_device loads after
        # we do), then we trigger an update here with the expectation that we got a
        # device registry update after the private ble device was created. There might
        # be other corner cases where we need to trigger our own update here, so test
        # carefully and completely if you are tempted to remove / alter this. Bermuda
        # will skip an update cycle if it detects one already in progress.
        # FIXME: self._async_update_data_internal()

    async def async_cleanup_device_registry_connections(self) -> None:
        """Canonicalise and deduplicate device registry connections for Bermuda devices."""
        mac_connection_types = {dr.CONNECTION_BLUETOOTH, dr.CONNECTION_NETWORK_MAC, "mac"}
        scanned = 0
        updated = 0
        registry = self.dr

        for device in list(registry.devices.values()):
            if not any(ident_domain == DOMAIN for ident_domain, _ in device.identifiers):
                continue

            scanned += 1
            original_connections = set(device.connections or set())
            normalized_connections: set[tuple[str, str]] = set()

            for conn_type, conn_value in original_connections:
                normalized_type = dr.CONNECTION_NETWORK_MAC if conn_type == "mac" else conn_type
                normalized_value = conn_value

                if normalized_type in mac_connection_types or is_mac_address(conn_value):
                    try:
                        normalized_value = normalize_mac(conn_value)
                    except ValueError:
                        normalized_value = conn_value

                normalized_connections.add((normalized_type, normalized_value))

            if normalized_connections != original_connections:
                # new_connections replaces the existing set (it is not merged), so legacy/duplicated
                # tuples are dropped when we write the canonicalized set back.
                registry.async_update_device(device.id, new_connections=normalized_connections)
                updated += 1

        if updated:
            _LOGGER.debug(
                "Normalized device registry connections for %d Bermuda devices (scanned %d)",
                updated,
                scanned,
            )

    @callback
    def async_handle_advert(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """
        Handle an incoming advert callback from the bluetooth integration.

        These should come in as adverts are received, rather than on our update schedule.
        The data *should* be as fresh as can be, but actually the backend only sends
        these periodically (mainly when the data changes, I think). So it's no good for
        responding to changing rssi values, but it *is* good for seeding our updates in case
        there are no defined sensors yet (or the defined ones are away).
        """
        # _LOGGER.debug(
        #     "New Advert! change: %s, scanner: %s mac: %s name: %s serviceinfo: %s",
        #     change,
        #     service_info.source,
        #     service_info.address,
        #     service_info.name,
        #     service_info,
        # )

        # If there are no active entities created after Bermuda's
        # initial setup, then no updates will be triggered on the co-ordinator.
        # So let's check if we haven't updated recently, and do so...
        if self.stamp_last_update < monotonic_time_coarse() - (UPDATE_INTERVAL * 2):
            self._async_update_data_internal()

    def _check_all_platforms_created(self, address):
        """Checks if all platforms have finished loading a device's entities."""
        dev = self._get_device(address)
        if dev is not None:
            if all(
                [
                    dev.create_sensor_done,
                    dev.create_tracker_done,
                    dev.create_number_done,
                    dev.create_select_done,
                ]
            ):
                dev.create_all_done = True

    def sensor_created(self, address):
        """Allows sensor platform to report back that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_sensor_done = True
            # _LOGGER.debug("Sensor confirmed created for %s", address)
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")
        self._check_all_platforms_created(address)

    def device_tracker_created(self, address):
        """Allows device_tracker platform to report back that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_tracker_done = True
            # _LOGGER.debug("Device_tracker confirmed created for %s", address)
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")
        self._check_all_platforms_created(address)

    def number_created(self, address):
        """Receives report from number platform that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_number_done = True
        self._check_all_platforms_created(address)

    def select_created(self, address: str) -> None:
        """Receives report from select platform that entities have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_select_done = True
        self._check_all_platforms_created(address)

    async def async_train_fingerprint(
        self,
        device_address: str,
        target_area_id: str,
        last_stamps: dict[str, float] | None = None,
    ) -> tuple[bool, dict[str, float]]:
        """
        Train fingerprint for a device in a specific area using current RSSI readings.

        Called when user manually selects a room via the training Select entity.
        Collects current RSSI readings from all visible scanners and feeds them
        into the AreaProfile for that area.

        IMPORTANT: This function only succeeds if at least one scanner has NEW data
        since the last call (based on last_stamps). This prevents over-confidence
        from re-reading the same cached RSSI values multiple times.

        Args:
            device_address: Address of the device to train
            target_area_id: Home Assistant area ID to train for
            last_stamps: Dict of scanner_addr -> last stamp from previous call.
                         If None or empty, any valid reading counts as "new".

        Returns:
            Tuple of (success: bool, current_stamps: dict[str, float])
            - success: True if training was successful with NEW data
            - current_stamps: Current timestamps for all visible scanners
              (caller should pass this as last_stamps on next call)

        """
        device = self._get_device(device_address)
        if device is None:
            _LOGGER.warning("Cannot train fingerprint: device %s not found", device_address)
            return (False, {})

        # FIX: Velocity Reset - When user manually trains, reset velocity history
        # This breaks the "Velocity Trap" where a device moving from Scanner A (12m)
        # to Scanner B (1m) gets stuck because the calculated velocity exceeds MAX_VELOCITY.
        # Manual training means "the device is HERE NOW" - any previous velocity
        # calculations are irrelevant and should not block acceptance of new readings.
        device.reset_velocity_history()

        # Collect current RSSI readings AND timestamps from all visible scanners
        nowstamp = monotonic_time_coarse()
        rssi_readings: dict[str, float] = {}
        current_stamps: dict[str, float] = {}
        primary_scanner_addr: str | None = None
        primary_rssi: float | None = None

        for advert in device.adverts.values():
            if (
                advert.rssi is not None
                and advert.scanner_address is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            ):
                rssi_readings[advert.scanner_address] = advert.rssi
                current_stamps[advert.scanner_address] = advert.stamp
                # Track the strongest signal as "primary" for the delta correlations
                if primary_rssi is None or advert.rssi > primary_rssi:
                    primary_rssi = advert.rssi
                    primary_scanner_addr = advert.scanner_address

        if len(rssi_readings) < 1:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Training for %s: no recent RSSI readings, waiting...",
                    device.name,
                )
            return (False, current_stamps)

        if primary_rssi is None or primary_scanner_addr is None:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Training for %s: no primary scanner identified, waiting...",
                    device.name,
                )
            return (False, current_stamps)

        # BUG 19 FIX: Only train if we have NEW advertisement data
        # Without this check, we'd re-read the same cached RSSI values multiple times,
        # causing over-confidence in the Kalman filter (many samples, but same data).
        # BLE trackers typically advertise every 1-10 seconds, so 0.5s polling would
        # read the same value 2-20 times before new data arrives.
        if last_stamps:
            has_new_data = False
            for scanner_addr, stamp in current_stamps.items():
                last_stamp = last_stamps.get(scanner_addr, 0.0)
                if stamp > last_stamp:
                    has_new_data = True
                    break

            if not has_new_data:
                # No new data yet - return current stamps so caller can retry
                return (False, current_stamps)

        # BUG 17 FIX: Use device.address (normalized) instead of device_address (raw parameter)
        # This ensures the correlations key matches the lookup key used elsewhere in the code.
        # Auto-learning and virtual distance lookup both use device.address, so training must too.
        normalized_address = device.address

        # Ensure device entry exists in correlations
        if normalized_address not in self.correlations:
            self.correlations[normalized_address] = {}

        # Create or get the AreaProfile for this area
        if target_area_id not in self.correlations[normalized_address]:
            self.correlations[normalized_address][target_area_id] = AreaProfile(area_id=target_area_id)

        # Build "other readings" (all except primary)
        other_readings = {addr: rssi for addr, rssi in rssi_readings.items() if addr != primary_scanner_addr}

        # Update the device-specific profile with BUTTON WEIGHT (stronger than automatic)
        # Button training uses update_button() which applies BUTTON_WEIGHT (2x) updates
        # to ensure manual corrections aren't overwhelmed by continuous automatic learning
        self.correlations[normalized_address][target_area_id].update_button(
            primary_rssi=primary_rssi,
            other_readings=other_readings,
            primary_scanner_addr=primary_scanner_addr,
        )

        # Update the device-independent room profile with BUTTON WEIGHT
        # This creates scanner-pair deltas that are shared across all devices
        if target_area_id not in self.room_profiles:
            self.room_profiles[target_area_id] = RoomProfile(area_id=target_area_id)
        self.room_profiles[target_area_id].update_button(rssi_readings)

        # BUG 17 DEBUG: Log button sample counts after training
        trained_profile = self.correlations[normalized_address][target_area_id]
        btn_counts = []
        for scanner_addr, abs_prof in trained_profile._absolute_profiles.items():  # noqa: SLF001
            btn_counts.append(f"{scanner_addr[-8:]}:{abs_prof.button_sample_count}")
        _LOGGER.info(
            "Trained fingerprint (button) for %s in area %s with %d scanners. "
            "Button counts: [%s], has_button_training=%s",
            device.name,
            target_area_id,
            len(rssi_readings),
            ", ".join(btn_counts),
            trained_profile.has_button_training,
        )

        # Save correlations immediately after manual training
        await self.correlation_store.async_save(self.correlations, self.room_profiles)
        self._last_correlation_save = nowstamp
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Saved correlations for %s after training. Total devices: %d",
                device.name,
                len(self.correlations),
            )

        return (True, current_stamps)

    async def async_reset_device_training(self, device_address: str) -> bool:
        """
        Reset all user training data for a device across ALL areas.

        This is the "nuclear option" for fixing incorrect manual training.
        It clears all button filter data (Frozen Layers) for this device,
        reverting to automatic learning (Shadow Learning) only.

        Use cases:
        - "Ghost Scanner" problem: Device was trained in wrong room
        - User wants to start fresh with automatic learning
        - Incorrect training in rooms that are no longer visible

        The auto-filter data is preserved, providing immediate fallback.

        Args:
            device_address: MAC address of the device to reset.

        Returns:
            True if any training data was reset, False if device had no training.

        """
        if device_address not in self.correlations:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "No training data found for device %s - nothing to reset",
                    device_address,
                )
            return False

        device_profiles = self.correlations[device_address]
        if not device_profiles:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Empty training data for device %s - nothing to reset",
                    device_address,
                )
            return False

        # Reset training in all areas for this device
        area_count = 0
        for area_id, profile in device_profiles.items():
            profile.reset_training()
            area_count += 1
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Reset training for device %s in area %s",
                    device_address,
                    area_id,
                )

        _LOGGER.info(
            "Reset all training data for device %s across %d areas",
            device_address,
            area_count,
        )

        # Persist changes immediately
        # FIX: BUG 4 - Add error handling to prevent silent persistence failures
        try:
            await self.correlation_store.async_save(self.correlations, self.room_profiles)
            self._last_correlation_save = monotonic_time_coarse()
        except (OSError, TypeError, ValueError):
            # Log error but don't fail - in-memory reset already happened
            # User will see the reset immediately, but if HA restarts before
            # a successful save, the old training data would be restored.
            _LOGGER.exception(
                "Failed to persist training reset for device %s. "
                "Reset is active in memory but may not survive restart.",
                device_address,
            )
            # Still return True - the in-memory reset succeeded
            # Next periodic save or manual action may succeed

        return True

    # def button_created(self, address):
    #     """Receives report from number platform that sensors have been set up."""
    #     dev = self._get_device(address)
    #     if dev is not None:
    #         dev.create_button_done = True
    #     self._check_all_platforms_created(address)

    def count_active_devices(self) -> int:
        """
        Returns the number of bluetooth devices that have recent timestamps.

        Useful as a general indicator of health
        """
        stamp = monotonic_time_coarse() - 10  # seconds
        fresh_count = 0
        for device in self.devices.values():
            if device.last_seen > stamp:
                fresh_count += 1
        return fresh_count

    def count_active_scanners(self, max_age: float = 10) -> int:
        """Returns count of scanners that have recently sent updates."""
        stamp = monotonic_time_coarse() - max_age  # seconds
        fresh_count = 0
        for scanner in self.get_active_scanner_summary():
            last_stamp = scanner.get("last_stamp", 0)
            if isinstance(last_stamp, str):
                try:
                    last_stamp = float(last_stamp)
                except ValueError:
                    last_stamp = 0.0
            if float(last_stamp) > stamp:
                fresh_count += 1
        return fresh_count

    def get_active_scanner_summary(self) -> list[dict[str, float | str]]:
        """
        Returns a list of dicts suitable for seeing which scanners
        are configured in the system and how long it has been since
        each has returned an advertisement.
        """
        stamp = monotonic_time_coarse()
        return [
            {
                "name": scannerdev.name,
                "address": scannerdev.address,
                "last_stamp": scannerdev.last_seen,
                "last_stamp_age": stamp - scannerdev.last_seen,
            }
            for scannerdev in self.get_scanners
        ]

    def _get_device(self, address: str) -> BermudaDevice | None:
        """Search for a device entry based on address."""
        try:
            return self.devices[normalize_address(address)]
        except KeyError:
            return None

    def _get_or_create_device(self, address: str) -> BermudaDevice:
        mac = normalize_address(address)
        try:
            return self.devices[mac]
        except KeyError:
            self.devices[mac] = device = BermudaDevice(mac, self)
            return device

    async def _async_update_data(self) -> bool:
        """Implementation of DataUpdateCoordinator update_data function."""
        # Load correlations on first update
        if not self._correlations_loaded:
            correlation_data = await self.correlation_store.async_load_all()
            self.correlations = correlation_data.device_profiles
            self.room_profiles = correlation_data.room_profiles
            self._correlations_loaded = True
            self._last_correlation_save = monotonic_time_coarse()
            _LOGGER.debug(
                "Loaded scanner correlations: %d devices, %d room profiles",
                len(self.correlations),
                len(self.room_profiles),
            )
            # BUG 17 DEBUG: Log button training status for all loaded profiles
            for dev_addr, areas in self.correlations.items():
                for area_id, profile in areas.items():
                    if profile.has_button_training:
                        btn_counts = []
                        for scanner, abs_p in profile._absolute_profiles.items():  # noqa: SLF001
                            if abs_p.button_sample_count > 0:
                                btn_counts.append(f"{scanner[-8:]}:{abs_p.button_sample_count}")
                        _LOGGER.info(
                            "Loaded button-trained profile: device=%s area=%s btn_profiles=[%s]",
                            dev_addr[-8:],
                            area_id,
                            ", ".join(btn_counts),
                        )

        result = self._async_update_data_internal()

        # Periodically save correlations
        nowstamp = monotonic_time_coarse()
        if nowstamp - self._last_correlation_save > CORRELATION_SAVE_INTERVAL:
            await self.correlation_store.async_save(self.correlations, self.room_profiles)
            self._last_correlation_save = nowstamp

        return result

    def _async_update_data_internal(self) -> bool:
        """
        The primary update loop that processes almost all data in Bermuda.

        This works only with local data, so should be cheap to run
        (no network requests made etc). This function takes care of:

        - gathering all bluetooth adverts since last run and saving them into
          Bermuda's device objects
        - Updating all metadata
        - Performing rssi and statistical calculations
        - Making area determinations
        - (periodically) pruning device entries

        """
        if self._waitingfor_load_manufacturer_ids:
            _LOGGER.debug("Waiting for BT data load...")
            return True
        if self.update_in_progress:
            # Eeep!
            _LOGGER_SPAM_LESS.warning("update_still_running", "Previous update still running, skipping this cycle.")
            return False
        self.update_in_progress = True

        try:  # so we can still clean up update_in_progress
            nowstamp = monotonic_time_coarse()

            # The main "get all adverts from the backend" part.
            result_gather_adverts = self._async_gather_advert_data()

            self.update_metadevices()

            # Calculate per-device data
            #
            # Scanner entries have been loaded up with latest data, now we can
            # process data for all devices over all scanners.
            for device in self.devices.values():
                # Recalculate smoothed distances, last_seen etc
                device.calculate_data()

            # Update scanner auto-calibration based on cross-visibility
            update_scanner_calibration(
                self.scanner_calibration,
                self._scanner_list,
                self.devices,
            )

            self._refresh_areas_by_min_distance()

            # We might need to freshen deliberately on first start if no new scanners
            # were discovered in the first scan update. This is likely if nothing has changed
            # since the last time we booted.
            # if self._do_full_scanner_init:
            #     if not self._refresh_scanners():
            #         # _LOGGER.debug("Failed to refresh scanners, likely config entry not ready.")
            #         # don't fail the update, just try again next time.
            #         # self.last_update_success = False
            #         pass

            # If any *configured* devices have not yet been seen, create device
            # entries for them so they will claim the restored sensors in HA
            # (this prevents them from restoring at startup as "Unavailable" if they
            # are not currently visible, and will instead show as "Unknown" for
            # sensors and "Away" for device_trackers).
            #
            # This isn't working right if it runs once. Bodge it for now (cost is low)
            # and sort it out when moving to device-based restoration (ie using DR/ER
            # to decide what devices to track and deprecating CONF_DEVICES)
            #
            configured_devices_option = self.options.get(CONF_DEVICES, [])
            if not isinstance(configured_devices_option, list):
                configured_devices_option = []
            # if not self._seed_configured_devices_done:
            for _source_address in configured_devices_option:
                self._get_or_create_device(_source_address)
            self._seed_configured_devices_done = True

            # Trigger creation of any new entities
            #
            # The devices are all updated now (and any new scanners and beacons seen have been added),
            # so let's ensure any devices that we create sensors for are set up ready to go.
            for address, device in self.devices.items():
                if device.create_sensor:
                    if not device.create_all_done:
                        _LOGGER.debug("Firing device_new for %s (%s)", device.name, address)
                        # Note that the below should be OK thread-wise, debugger indicates this is being
                        # called by _run in events.py, so pretty sure we are "in the event loop".
                        async_dispatcher_send(self.hass, SIGNAL_DEVICE_NEW, address)

            # Device Pruning (only runs periodically)
            self.prune_devices()

        finally:
            # end of async update
            self.update_in_progress = False

        self.stamp_last_update_started = nowstamp
        self.stamp_last_update = monotonic_time_coarse()
        self.last_update_success = True
        return result_gather_adverts

    def _async_gather_advert_data(self) -> bool:
        """Perform the gathering of backend Bluetooth Data and updating scanners and devices."""
        # Initialise ha_scanners if we haven't already
        if self._scanner_init_pending:
            self._refresh_scanners(force=True)

        for ha_scanner in self._hascanners:
            # Create / Get the BermudaDevice for this scanner
            scanner_device = self._get_device(ha_scanner.source)

            if scanner_device is None:
                # Looks like a scanner we haven't met, refresh the list.
                self._refresh_scanners(force=True)
                scanner_device = self._get_device(ha_scanner.source)

            if scanner_device is None:
                # Highly unusual. If we can't find an entry for the scanner
                # maybe it's from an integration that's not yet loaded, or
                # perhaps it's an unexpected type that we don't know how to
                # find.
                _LOGGER_SPAM_LESS.error(
                    f"missing_scanner_entry_{ha_scanner.source}",
                    "Failed to find config for scanner %s, this is probably a bug.",
                    ha_scanner.source,
                )
                continue

            scanner_device.async_as_scanner_update(ha_scanner)

            # Now go through the scanner's adverts and send them to our device objects.
            for bledevice, advertisementdata in ha_scanner.discovered_devices_and_advertisement_data.values():
                if adstamp := scanner_device.async_as_scanner_get_stamp(bledevice.address):
                    if adstamp < self.stamp_last_update_started - 3:
                        # skip older adverts that should already have been processed
                        continue
                if advertisementdata.rssi == -127:
                    # BlueZ is pushing bogus adverts for paired but absent devices.
                    continue

                device = self._get_or_create_device(bledevice.address)
                device.process_advertisement(scanner_device, advertisementdata)

                service_data_raw = advertisementdata.service_data or {}
                service_data = cast("Mapping[str | int, Any]", service_data_raw)
                self.fmdn.handle_advertisement(device, service_data)

        # end of for ha_scanner loop
        return True

    def prune_devices(self, force_pruning: bool = False) -> None:  # noqa: C901, FBT001
        """
        Scan through all collected devices, and remove those that meet Pruning criteria.

        By default no pruning will be done if it has been performed within the last
        PRUNE_TIME_INTERVAL, unless the force_pruning flag is set to True.
        """
        if self.stamp_last_prune > monotonic_time_coarse() - PRUNE_TIME_INTERVAL and not force_pruning:
            # We ran recently enough, bail out.
            return
        # stamp the run.
        nowstamp = self.stamp_last_prune = monotonic_time_coarse()
        stamp_known_irk = nowstamp - PRUNE_TIME_KNOWN_IRK
        stamp_fmdn = nowstamp - PRUNE_TIME_FMDN
        stamp_unknown_irk = nowstamp - PRUNE_TIME_UNKNOWN_IRK

        # Prune redaction data
        if self.stamp_redactions_expiry is not None and self.stamp_redactions_expiry < nowstamp:
            _LOGGER.debug("Clearing redaction data (%d items)", len(self.redactions))
            self.redactions.clear()
            self.stamp_redactions_expiry = None

        # Prune any IRK MACs that have expired
        self.irk_manager.async_prune()

        # Prune any FMDN EIDs that have expired
        self.fmdn.manager.async_prune()

        # Prune devices.
        prune_list: list[str] = []  # list of addresses to be pruned
        prunable_stamps: dict[str, float] = {}  # dict of potential prunees if we need to be more aggressive.

        metadevice_source_keepers = set()
        for metadevice in self.metadevices.values():
            if len(metadevice.metadevice_sources) > 0:
                # Always keep the most recent source, which we keep in index 0.
                # This covers static iBeacon sources, and possibly IRKs that might exceed
                # the spec lifetime but are going stale because they're away for a bit.
                _first = True
                for address in metadevice.metadevice_sources:
                    if _device := self._get_device(address):
                        if self.fmdn.prune_source(_device, stamp_fmdn, prune_list):
                            continue
                        if _first or _device.last_seen > stamp_known_irk:
                            # The source has been seen within the spec's limits, keep it.
                            metadevice_source_keepers.add(address)
                            _first = False
                        else:
                            # It's too old to be an IRK, and otherwise we'll auto-detect it,
                            # so let's be rid of it.
                            prune_list.append(address)

        for device_address, device in self.devices.items():
            if device_address in prune_list:
                continue
            # Prune any devices that haven't been heard from for too long, but only
            # if we aren't actively tracking them and it's a traditional MAC address.
            # We just collect the addresses first, and do the pruning after exiting this iterator
            #
            # Reduced selection criteria - basically if if's not:
            # - a scanner (beacuse we need those!)
            # - any metadevice less than 15 minutes old (PRUNE_TIME_KNOWN_IRK)
            # - a private_ble device (because they will re-create anyway, plus we auto-sensor them
            # - create_sensor
            # then it should be up for pruning. A stale iBeacon that we don't actually track
            # should totally be pruned if it's no longer around.
            if (
                device_address not in metadevice_source_keepers
                and device_address not in self.metadevices
                and device_address not in self.scanner_list
                and (not device.create_sensor)  # Not if we track the device
                and (not device.is_scanner)  # redundant, but whatevs.
                and device.address_type != BDADDR_TYPE_NOT_MAC48
            ):
                if device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE:
                    # This is an *UNKNOWN* IRK source address, or a known one which is
                    # well and truly stale (ie, not in keepers).
                    # We prune unknown irk's aggressively because they pile up quickly
                    # in high-density situations, and *we* don't need to hang on to new
                    # enrollments because we'll seed them from PBLE.
                    if device.last_seen < stamp_unknown_irk:
                        _LOGGER.debug(
                            "Marking stale (%ds) Unknown IRK address for pruning: [%s] %s",
                            nowstamp - device.last_seen,
                            device_address,
                            device.name,
                        )
                        prune_list.append(device_address)
                    elif device.last_seen < nowstamp - 200:  # BlueZ cache time
                        # It's not stale, but we will prune it if we can't make our
                        # quota of PRUNE_MAX_COUNT we'll shave these off too.

                        # Note that because BlueZ doesn't give us timestamps, we guess them
                        # based on whether the rssi has changed. If we delete our existing
                        # device we have nothing to compare too and will forever churn them.
                        # This can change if we drop support for BlueZ or we find a way to
                        # make stamps (we could also just keep a separate list but meh)
                        prunable_stamps[device_address] = device.last_seen

                elif device.last_seen < nowstamp - PRUNE_TIME_DEFAULT:
                    # It's a static address, and stale.
                    _LOGGER.debug(
                        "Marking old device entry for pruning: %s",
                        device.name,
                    )
                    prune_list.append(device_address)
                else:
                    # Device is static, not tracked, not so old, but we might have to prune it anyway
                    prunable_stamps[device_address] = device.last_seen

            # Do nothing else at this level without excluding the keepers first.

        prune_quota_shortfall = len(self.devices) - len(prune_list) - PRUNE_MAX_COUNT
        if prune_quota_shortfall > 0:
            # We need to find more addresses to prune. Perhaps we live
            # in a busy train station, or are under some sort of BLE-MAC
            # DOS-attack.
            if len(prunable_stamps) > 0:
                # Sort the prunables by timestamp ascending
                sorted_addresses = sorted([(v, k) for k, v in prunable_stamps.items()])
                cutoff_index = min(len(sorted_addresses), prune_quota_shortfall)

                if cutoff_index > 0:
                    _LOGGER.debug(
                        "Prune quota short by %d. Pruning %d extra devices (down to age %0.2f seconds)",
                        prune_quota_shortfall,
                        cutoff_index,
                        nowstamp - sorted_addresses[cutoff_index - 1][0],
                    )
                # pylint: disable-next=unused-variable
                for _stamp, address in sorted_addresses[:cutoff_index]:
                    prune_list.append(address)
            else:
                _LOGGER.warning(
                    "Need to prune another %s devices to make quota, but no extra prunables available",
                    prune_quota_shortfall,
                )
        else:
            _LOGGER.debug(
                "Pruning %d available MACs, we are inside quota by %d.", len(prune_list), prune_quota_shortfall * -1
            )

        # ###############################################
        # Prune_list is now ready to action. It contains no keepers, and is already
        # expanded if necessary to meet quota, as much as we can.

        # Prune the source devices
        for device_address in prune_list:
            _LOGGER.debug("Acting on prune list for %s", device_address)
            del self.devices[device_address]
            # FIX: BUG 7 - Also remove from device_ukfs to prevent memory leak
            # Without this, UKF states for pruned devices accumulate forever
            self.device_ukfs.pop(device_address, None)

        # Clean out the scanners dicts in metadevices and scanners
        # (scanners will have entries if they are also beacons, although
        # their addresses should never get stale, but one day someone will
        # have a beacon that uses randomised source addresses for some reason.
        #
        # Just brute-force all devices, because it was getting a bit hairy
        # ensuring we hit the right ones, and the cost is fairly low and periodic.
        for device in self.devices.values():
            # if (
            #     device.is_scanner
            #     or METADEVICE_PRIVATE_BLE_DEVICE in device.metadevice_type
            #     or METADEVICE_IBEACON_DEVICE in device.metadevice_type
            # ):
            # clean out the metadevice_sources field
            for address in prune_list:
                if address in device.metadevice_sources:
                    device.metadevice_sources.remove(address)

            # clean out the device/scanner advert pairs
            for advert_tuple in list(device.adverts.keys()):
                if device.adverts[advert_tuple].device_address in prune_list:
                    _LOGGER.debug(
                        "Pruning metadevice advert %s aged %ds",
                        advert_tuple,
                        nowstamp - device.adverts[advert_tuple].stamp,
                    )
                    del device.adverts[advert_tuple]

    def discover_private_ble_metadevices(self):
        """
        Access the Private BLE Device integration to find metadevices to track.

        This function sets up the skeleton metadevice entry for Private BLE (IRK)
        devices, ready for update_metadevices to manage.
        """
        if self._do_private_device_init:
            self._do_private_device_init = False
            _LOGGER.debug("Refreshing Private BLE Device list")

            # Iterate through the Private BLE Device integration's entities,
            # and ensure for each "device" we create a source device.
            # pb here means "private ble device"
            pb_entries = self.hass.config_entries.async_entries(DOMAIN_PRIVATE_BLE_DEVICE, include_disabled=False)
            for pb_entry in pb_entries:
                pb_entities = self.er.entities.get_entries_for_config_entry_id(pb_entry.entry_id)
                # This will be a list of entities for a given private ble device,
                # let's pull out the device_tracker one, since it has the state
                # info we need.
                for pb_entity in pb_entities:
                    if pb_entity.domain == Platform.DEVICE_TRACKER:
                        # We found a *device_tracker* entity for the private_ble device.
                        _LOGGER.debug(
                            "Found a Private BLE Device Tracker! %s",
                            pb_entity.entity_id,
                        )

                        # Grab the device entry (for the name, mostly)
                        if pb_entity.device_id is not None:
                            pb_device = self.dr.async_get(pb_entity.device_id)
                        else:
                            pb_device = None

                        # Grab the current state (so we can access the source address attrib)
                        pb_state = self.hass.states.get(pb_entity.entity_id)

                        if pb_state:  # in case it's not there yet
                            pb_source_address = pb_state.attributes.get("current_address", None)
                        else:
                            # Private BLE Device hasn't yet found a source device
                            pb_source_address = None

                        # Get the IRK of the device, which we will use as the address
                        # for the metadevice.
                        # As of 2024.4.0b4 Private_ble appends _device_tracker to the
                        # unique_id of the entity, while we really want to know
                        # the actual IRK, so handle either case by splitting it:
                        _irk = pb_entity.unique_id.split("_")[0]

                        # Create our Meta-Device and tag it up...
                        metadevice = self._get_or_create_device(_irk)
                        # Since user has already configured the Private BLE Device, we
                        # always create sensors for them.
                        metadevice.create_sensor = True

                        # Set a nice name
                        if pb_device:
                            metadevice.name_by_user = pb_device.name_by_user
                            metadevice.name_devreg = pb_device.name
                            metadevice.make_name()

                        # Ensure we track this PB entity so we get source address updates.
                        if pb_entity.entity_id not in self.pb_state_sources:
                            self.pb_state_sources[pb_entity.entity_id] = None  # FIXME: why none?

                        # Add metadevice to list so it gets included in update_metadevices
                        if metadevice.address not in self.metadevices:
                            self.metadevices[metadevice.address] = metadevice

                        if pb_source_address is not None:
                            # We've got a source MAC address!
                            try:
                                pb_source_address = normalize_mac(pb_source_address)
                            except ValueError:
                                _LOGGER.debug("Skipping invalid PB source address: %s", pb_source_address)
                                pb_source_address = None

                            if pb_source_address is not None:
                                # Set up and tag the source device entry
                                source_device = self._get_or_create_device(pb_source_address)
                                source_device.metadevice_type.add(METADEVICE_TYPE_PRIVATE_BLE_SOURCE)

                                # Add source address. Don't remove anything, as pruning takes care of that.
                                if pb_source_address not in metadevice.metadevice_sources:
                                    metadevice.metadevice_sources.insert(0, pb_source_address)

                                # Update state_sources so we can track when it changes
                                self.pb_state_sources[pb_entity.entity_id] = pb_source_address

                        else:
                            _LOGGER.debug(
                                "No address available for PB Device %s",
                                pb_entity.entity_id,
                            )

    def register_ibeacon_source(self, source_device: BermudaDevice) -> None:
        """
        Create or update the meta-device for tracking an iBeacon.

        This should be called each time we discover a new address advertising
        an iBeacon. This might happen only once at startup, but will also
        happen each time a new MAC address is used by a given iBeacon,
        or each time an existing MAC sends a *new* iBeacon(!)

        This does not update the beacon's details (distance etc), that is done
        in the update_metadevices function after all data has been gathered.
        """
        if METADEVICE_TYPE_IBEACON_SOURCE not in source_device.metadevice_type:
            _LOGGER.error(
                "Only IBEACON_SOURCE devices can be used to see a beacon metadevice. %s is not",
                source_device.name,
            )
        if source_device.beacon_unique_id is None:
            _LOGGER.error("Source device %s is not a valid iBeacon!", source_device.name)
        else:
            metadevice = self._get_or_create_device(source_device.beacon_unique_id)
            if len(metadevice.metadevice_sources) == 0:
                # #### NEW METADEVICE #####
                # (do one-off init stuff here)
                if metadevice.address not in self.metadevices:
                    self.metadevices[metadevice.address] = metadevice

                # Copy over the beacon attributes
                metadevice.name_bt_serviceinfo = source_device.name_bt_serviceinfo
                metadevice.name_bt_local_name = source_device.name_bt_local_name
                metadevice.beacon_unique_id = source_device.beacon_unique_id
                metadevice.beacon_major = source_device.beacon_major
                metadevice.beacon_minor = source_device.beacon_minor
                metadevice.beacon_power = source_device.beacon_power
                metadevice.beacon_uuid = source_device.beacon_uuid

                # Check if we should set up sensors for this beacon
                configured_devices_option = self.options.get(CONF_DEVICES, [])
                if not isinstance(configured_devices_option, list):
                    configured_devices_option = []
                configured_devices = {normalize_address(addr) for addr in configured_devices_option}
                if metadevice.address in configured_devices:
                    # This is a meta-device we track. Flag it for set-up:
                    metadevice.create_sensor = True

            # #### EXISTING METADEVICE ####
            # (only do things that might have to change when MAC address cycles etc)

            if source_device.address not in metadevice.metadevice_sources:
                # We have a *new* source device.
                # insert this device as a known source
                metadevice.metadevice_sources.insert(0, source_device.address)

                # If we have a new / better name, use that..
                metadevice.name_bt_serviceinfo = metadevice.name_bt_serviceinfo or source_device.name_bt_serviceinfo
                metadevice.name_bt_local_name = metadevice.name_bt_local_name or source_device.name_bt_local_name

    def update_metadevices(self):
        """
        Create or update iBeacon, Private_BLE and other meta-devices from
        the received advertisements.

        This must be run on each update cycle, after the calculations for each source
        device is done, since we will copy their results into the metadevice.

        Area matching and trilateration will be performed *after* this, as they need
        to consider the full collection of sources, not just the ones of a single
        source device.
        """
        # First seed the Private BLE metadevice skeletons. It will only do anything
        # if the self._do_private_device_init flag is set.
        # FIXME: Can we delete this? pble's should create at realtime as they
        # are detected now.
        self.discover_private_ble_metadevices()

        # Seed the FMDN (googlefindmy) metadevice skeletons. It will only do anything
        # if the self._do_fmdn_device_init flag is set.
        self.fmdn.discover_metadevices()

        # iBeacon devices should already have their metadevices created, so nothing more to
        # set up for them.

        # Track which source devices have had ref_power set this cycle to prevent
        # multiple metadevices from "fighting" over a shared source's ref_power.
        # This fixes the dual-stack device issue (e.g., iBeacon + FMDN on same device).
        ref_power_set_this_cycle: set[str] = set()

        for metadevice in self.metadevices.values():
            # Find every known source device and copy their adverts in.

            # Keep track of whether we want to recalculate the name fields at the end.
            _want_name_update = False
            _sources_to_remove = []

            for source_address in metadevice.metadevice_sources:
                # Get the BermudaDevice holding those adverts
                # TODO: Verify it's OK to not create here. Problem is that if we do create,
                # it causes a binge/purge cycle during pruning since it has no adverts on it.
                source_device = self._get_device(source_address)
                if source_device is None:
                    # No ads current in the backend for this one. Not an issue, the mac might be old
                    # or now showing up yet.
                    # _LOGGER_SPAM_LESS.debug(
                    #     f"metaNoAdsFor_{metadevice.address}_{source_address}",
                    #     "Metadevice %s: no adverts for source MAC %s found during update_metadevices",
                    #     metadevice.__repr__(),
                    #     source_address,
                    # )
                    continue

                if (
                    METADEVICE_IBEACON_DEVICE in metadevice.metadevice_type
                    and metadevice.beacon_unique_id != source_device.beacon_unique_id
                ):
                    # This source device no longer has the same ibeacon uuid+maj+min as
                    # the metadevice has.
                    # Some iBeacons (specifically Bluecharms) change uuid on movement.
                    #
                    # This source device has changed its uuid, so we won't track it against
                    # this metadevice any more / for now, and we will also remove
                    # the existing scanner entries on the metadevice, to ensure it goes
                    # `unknown` immediately (assuming no other source devices show up)
                    #
                    # Note that this won't quick-away devices that change their MAC at the
                    # same time as changing their uuid (like manually altering the beacon
                    # in an Android 15+), since the old source device will still be a match.
                    # and will be subject to the nomal DEVTRACK_TIMEOUT.
                    #
                    _LOGGER.debug(
                        "Source %s for metadev %s changed iBeacon identifiers, severing", source_device, metadevice
                    )
                    for key_address, key_scanner in list(metadevice.adverts):
                        if key_address == source_device.address:
                            del metadevice.adverts[(key_address, key_scanner)]
                    if source_device.address in metadevice.metadevice_sources:
                        # Remove this source from the list once we're done iterating on it
                        _sources_to_remove.append(source_device.address)
                    continue  # to next metadevice_source

                # Copy every ADVERT_TUPLE into our metadevice
                for advert_tuple in source_device.adverts:
                    metadevice.adverts[advert_tuple] = source_device.adverts[advert_tuple]

                # Update last_seen if the source is newer.
                if metadevice.last_seen < source_device.last_seen:
                    metadevice.last_seen = source_device.last_seen

                # If not done already, set the source device's ref_power from our own. This will cause
                # the source device and all its scanner entries to update their
                # distance measurements. This won't affect Area wins though, because
                # they are "relative", not absolute.

                # Dual-stack device guard: If multiple metadevices share the same source
                # (e.g., iBeacon + FMDN on same device), we need to prevent them from
                # "fighting" over ref_power. Priority rules:
                # 1. First metadevice to touch a source "claims" it for this cycle
                # 2. User-configured ref_power (non-zero) always takes priority over default
                # 3. Default (zero) ref_power never overwrites a non-zero value

                # Note we are setting the ref_power on the source_device, not the
                # individual scanner entries (it will propagate to them though)
                should_set_ref_power = False
                if source_address not in ref_power_set_this_cycle:
                    # First metadevice to touch this source this cycle - claim it
                    ref_power_set_this_cycle.add(source_address)
                    # Only change ref_power if different AND (this metadevice has
                    # user-configured value OR source has default value)
                    if source_device.ref_power != metadevice.ref_power:
                        # Don't let default (0) overwrite a calibrated source value
                        if metadevice.ref_power != 0 or source_device.ref_power == 0:
                            should_set_ref_power = True
                elif metadevice.ref_power not in (0, source_device.ref_power):
                    # Source already claimed, but this metadevice has user-configured
                    # ref_power which takes priority
                    should_set_ref_power = True

                if should_set_ref_power:
                    source_device.set_ref_power(metadevice.ref_power)

                # anything that isn't already set to something interesting, overwrite
                # it with the new device's data.
                for key, val in source_device.items():
                    if val is any(
                        [
                            source_device.name_bt_local_name,
                            source_device.name_bt_serviceinfo,
                            source_device.manufacturer,
                        ]
                    ) and metadevice[key] in [None, False]:
                        metadevice[key] = val
                        _want_name_update = True

                if _want_name_update:
                    metadevice.make_name()

                # Anything that's VERY interesting, overwrite it regardless of what's already there:
                # INTERESTING:
                for key, val in source_device.items():
                    if val is any(
                        [
                            source_device.beacon_major,
                            source_device.beacon_minor,
                            source_device.beacon_power,
                            source_device.beacon_unique_id,
                            source_device.beacon_uuid,
                        ]
                    ):
                        metadevice[key] = val
                        # _want_name_update = True
            # Done iterating sources, remove any to be dropped
            for source in _sources_to_remove:
                metadevice.metadevice_sources.remove(source)
            if _want_name_update:
                metadevice.make_name()

    def dt_mono_to_datetime(self, stamp: float) -> datetime:
        """Given a monotonic timestamp, convert to datetime object."""
        age = float(monotonic_time_coarse() - stamp)
        return now() - timedelta(seconds=age)

    def dt_mono_to_age(self, stamp: float) -> str:
        """Convert monotonic timestamp to age (eg: "6 seconds ago")."""
        return get_age(self.dt_mono_to_datetime(stamp))

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
        # The adaptive timeout is calculated per-advert based on MAX(observed intervals) x 2,
        # which handles devices with variable advertisement intervals (e.g., smartphone deep sleep).
        max_age = getattr(advert, "adaptive_timeout", None) or AREA_MAX_AD_AGE_DEFAULT
        # Clamp to absolute limit to prevent runaway values
        max_age = min(max_age, AREA_MAX_AD_AGE_LIMIT)

        if advert.stamp < nowstamp - max_age:
            return None

        hist_distances = [
            value for value in getattr(advert, "hist_distance_by_interval", []) if isinstance(value, (int, float))
        ]
        if hist_distances:
            return hist_distances[0]

        return None

    def resolve_area_name(self, area_id: str | None) -> str | None:
        """
        Given an area_id, return the current area name.

        Will return None if the area id does *not* resolve to a single
        known area name.
        """
        if area_id is None:
            return None

        areas = self.ar.async_get_area(area_id)
        if hasattr(areas, "name"):
            return getattr(areas, "name", "invalid_area")
        return None

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
            device_address: The device's address.
            area_id: The area to check confidence for.
            primary_rssi: RSSI from the primary scanner.
            current_readings: Map of scanner_id to RSSI for all visible scanners.

        Returns:
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

        # FIX: Fehler 1 - Integrate absolute RSSI z-scores to detect far-field false positives.
        # A device at -90dB should NOT match a room learned at -50dB, even if relative
        # deltas between scanners happen to match (vector shape matches but magnitude differs).
        absolute_z_scores = profile.get_absolute_z_scores(current_readings)
        if absolute_z_scores:
            # Calculate max absolute z-score - high values indicate wrong magnitude
            max_abs_z = max(z for _, z in absolute_z_scores)
            # If absolute RSSI is >3 standard deviations from learned value,
            # heavily penalize confidence regardless of how well deltas match
            if max_abs_z > 3.0:
                # Exponential penalty: z=3 -> 0.5x, z=4 -> 0.25x, z=5 -> 0.125x
                absolute_penalty: float = 0.5 ** (max_abs_z - 2.0)
                delta_confidence: float = z_scores_to_confidence(z_scores)
                return float(delta_confidence * absolute_penalty)

        return z_scores_to_confidence(z_scores)

    def _resolve_floor_id_for_area(self, area_id: str | None) -> str | None:
        """
        Resolve floor_id for an area_id using the Home Assistant area registry.

        This is essential for scannerless rooms where we can't get floor_id from
        a scanner_device - we must look up the area directly.
        """
        if area_id is None:
            return None
        area = self.ar.async_get_area(area_id)
        if area is not None:
            return getattr(area, "floor_id", None)
        return None

    def _area_has_scanner(self, area_id: str) -> bool:
        """
        Check if an area has at least one scanner assigned to it.

        Args:
            area_id: The Home Assistant area ID to check.

        Returns:
            True if the area contains at least one scanner device.

        """
        return any(scanner.area_id == area_id for scanner in self._scanners)

    def _calculate_virtual_distance(self, score: float, max_radius: float) -> float:
        """
        Convert a UKF fingerprint match score to a virtual distance.

        Uses a scaled quadratic formula that rewards medium scores (0.3-0.5)
        more aggressively than linear, allowing scannerless rooms to compete
        against physical scanners through walls.

        Formula: max_radius * SCALE * (1 - score)²

        Args:
            score: UKF match score (0.0 to 1.0)
            max_radius: Maximum radius from configuration

        Returns:
            Virtual distance in meters. Lower scores produce larger distances.

        """
        score_clamped = max(VIRTUAL_DISTANCE_MIN_SCORE, min(1.0, score))
        return max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - score_clamped) ** 2)

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
            device: The device to calculate virtual distances for.
            rssi_readings: Current RSSI readings from all visible scanners.

        Returns:
            Dict mapping area_id to virtual distance (meters) for scannerless rooms.

        """
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
        # FIX: BUG 16 - UKF must be created HERE if it doesn't exist, because
        # _refresh_area_by_ukf() may have returned early (e.g., single scanner)
        # before creating the UKF. We need the UKF to call match_fingerprints().
        if device.address not in self.device_ukfs:
            self.device_ukfs[device.address] = UnscentedKalmanFilter()

        ukf = self.device_ukfs[device.address]

        # Update UKF with current readings before matching
        # This ensures the UKF state reflects current RSSI values
        ukf.predict(dt=UPDATE_INTERVAL)
        ukf.update_multi(rssi_readings)

        # Get all matches from UKF
        matches = ukf.match_fingerprints(device_profiles, self.room_profiles)

        # DEBUG: Log what we're working with for scannerless room diagnosis
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Virtual distance check for %s: %d device_profiles, %d matches, rssi_readings=%s, ukf_scanners=%s",
                device.name,
                len(device_profiles),
                len(matches),
                list(rssi_readings.keys()),
                ukf.scanner_addresses,
            )
            for area_id, profile in device_profiles.items():
                has_btn = profile.has_button_training
                has_scanner = self._area_has_scanner(area_id)
                # BUG 17 DEBUG: Show button sample counts for each absolute profile
                abs_details = []
                if hasattr(profile, "_absolute_profiles"):
                    for scanner_addr, abs_prof in profile._absolute_profiles.items():  # noqa: SLF001
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

        for area_id, _d_squared, score in matches:
            # Only consider button-trained profiles (explicit user intent)
            # FIX: Use different variable name to avoid mypy no-redef error (line 1909 uses 'profile')
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

        # Collect RSSI readings from all visible scanners
        rssi_readings: dict[str, float] = {}
        for advert in device.adverts.values():
            if (
                advert.rssi is not None
                and advert.scanner_address is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            ):
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

                    # Allow up to 3 standard deviations from expected
                    rssi_threshold = 3.0 * math.sqrt(max(rssi_variance, 4.0))

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
            # No usable profile - fall back to min_distance
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
            return False

        # Match against both device-specific and room-level fingerprints
        matches = ukf.match_fingerprints(device_profiles, self.room_profiles)

        if not matches:
            return False

        # Get best match
        best_area_id, _d_squared, match_score = matches[0]

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
            for area_id, _d_sq, score in matches:
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
                return True
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
            # Scanner-less room: UKF matched an area with no scanner.
            # Use the best available advert (strongest RSSI) and override its area.
            strongest_rssi = -999.0
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
                return False

            scanner_less_room = True

        # Track whether current area is scannerless (for stickiness in future cycles)
        device._ukf_scannerless_area = scanner_less_room  # type: ignore[attr-defined]  # noqa: SLF001

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
            strongest_visible_rssi = -999.0

            for advert in device.adverts.values():
                if (
                    advert.rssi is not None
                    and advert.stamp is not None
                    and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                    and advert.rssi > strongest_visible_rssi
                ):
                    strongest_visible_rssi = advert.rssi

            # Only apply sanity check when UKF confidence is low AND signal is much weaker
            if (
                effective_match_score < UKF_LOW_CONFIDENCE_THRESHOLD
                and best_advert_rssi is not None
                and strongest_visible_rssi > -999.0
                and strongest_visible_rssi - best_advert_rssi > UKF_RSSI_SANITY_MARGIN
            ):
                # Low confidence UKF picked a room with weak signal - suspicious
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF sanity check failed for %s: UKF picked %s (score=%.2f, RSSI %.1f) but "
                        "strongest signal is %.1f dB stronger - falling back to min-distance",
                        device.name,
                        best_area_id,
                        effective_match_score,
                        best_advert_rssi,
                        strongest_visible_rssi - best_advert_rssi,
                    )
                return False

        # DISTANCE-BASED SANITY CHECK (BUG 14):
        # When a device is VERY close to a scanner, it's almost certainly in that
        # scanner's room. UKF fingerprints can be wrong (bad training), but physical
        # distance doesn't lie. This prevents the bug where UKF picks a room 2 floors
        # away when the device is 1.6m from a scanner.
        #
        # Only reject UKF if:
        # 1. There's a scanner VERY close (<2m) to the device
        # 2. UKF picked a DIFFERENT area than that scanner's area
        # 3. The scanner has a valid area assigned
        proximity_threshold = 2.0  # meters - very close means almost certainly in that room
        nearest_scanner_distance = 999.0
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

        if (
            nearest_scanner_distance < proximity_threshold
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
                return False

            # Same floor but different room while very close - allow only with very high confidence
            if effective_match_score < 0.85:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF distance sanity check FAILED for %s: Device is %.1fm from scanner "
                        "in %s, UKF picked %s with score %.2f < 0.85 - falling back to min-distance",
                        device.name,
                        nearest_scanner_distance,
                        nearest_scanner_area_id,
                        best_area_id,
                        effective_match_score,
                    )
                return False

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

        # If same area as current, just refresh the selection
        # FIX: FEHLER 1 (continued) - Compare with device.area_id, not current_area_advert.area_id
        if current_device_area_id is not None and best_area_id == current_device_area_id:
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )
            return True

        # If no current area, bootstrap immediately
        # FIX: FEHLER 1 (continued) - Check device.area_id, not current_area_advert
        if current_device_area_id is None:
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )
            return True

        # Determine streak target based on floor change
        streak_target = CROSS_FLOOR_STREAK if is_cross_floor else SAME_FLOOR_STREAK

        # Update streak counter
        if device.pending_area_id == best_area_id and device.pending_floor_id == winner_floor_id:
            # Same target as before - increment streak
            device.pending_streak += 1
        elif device.pending_area_id is not None and device.pending_area_id != best_area_id:
            # Different target - reset streak to new candidate
            device.pending_area_id = best_area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1
        else:
            # First pending or same floor different area
            device.pending_area_id = best_area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1

        # Check if streak meets threshold
        if device.pending_streak >= streak_target:
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )
        # Streak not reached - keep current area
        # NOTE: Use device.area_advert here (the actual advert object), not current_device_area_id.
        # apply_scanner_selection needs an advert object. The area_id determination above
        # correctly uses device.area_id, but the actual selection still needs the advert.
        elif device.area_advert is not None:
            device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)

        return True

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
        # This mirrors the learning logic in _refresh_area_by_min_distance
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

            if other_readings:
                # Ensure device entry exists in correlations
                if device.address not in self.correlations:
                    self.correlations[device.address] = {}
                # Ensure area entry exists for this device
                if best_area_id not in self.correlations[device.address]:
                    self.correlations[device.address][best_area_id] = AreaProfile(
                        area_id=best_area_id,
                    )
                # Update the device-specific profile
                self.correlations[device.address][best_area_id].update(
                    primary_rssi=best_advert.rssi,
                    other_readings=other_readings,
                    primary_scanner_addr=best_advert.scanner_address,
                )

                # Also update the device-independent room profile
                all_readings = dict(other_readings)
                if best_advert.scanner_address is not None:
                    all_readings[best_advert.scanner_address] = best_advert.rssi
                if best_area_id not in self.room_profiles:
                    self.room_profiles[best_area_id] = RoomProfile(area_id=best_area_id)
                self.room_profiles[best_area_id].update(all_readings)

    def _refresh_areas_by_min_distance(self) -> None:
        """Set area for ALL devices based on UKF+RoomProfile or min-distance fallback."""
        # Check if we have mature room profiles (at least 2 scanner-pairs with 30+ samples)
        has_mature_profiles = any(profile.mature_pair_count >= 2 for profile in self.room_profiles.values())

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
            device: The BermudaDevice to determine area for
            has_mature_profiles: Whether the system has mature RoomProfiles globally

        """
        # Skip scanners and devices not being tracked
        if device.is_scanner or not (device.create_sensor or device.create_tracker_done):
            return

        # Check if device is manually locked to an area
        if device.area_locked_id is not None:
            # Device is locked by user selection for training.
            # Only unlock if the locked scanner truly disappears (no advert at all).
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
                    # Locked scanner still has an advert - keep lock active.
                    # FIX: ACTIVE OVERRIDE - Set the device area to the locked area immediately.
                    # Previously, area_locked only prevented changes (guard) but didn't SET
                    # the area. This caused the UI to show the wrong room during training.
                    device.update_area_and_floor(device.area_locked_id)
                    return
            else:
                # Scannerless room: no specific scanner to track, just force the area.
                # FIX: ACTIVE OVERRIDE for scannerless rooms.
                device.update_area_and_floor(device.area_locked_id)
                return

        # Primary: UKF with RoomProfile (when profiles are mature)
        # Fallback: Simple min-distance (bootstrap phase)
        #
        # FIX: Fehler 4 - Allow UKF for "scannerless rooms" even without mature global profiles.
        # A scannerless room can ONLY be detected via UKF+fingerprints (min-distance fails
        # because there's no scanner in that room). Previously, UKF required global
        # has_mature_profiles, blocking newly-trained scannerless rooms for days/weeks.
        # Now we allow UKF if EITHER global profiles are mature OR this specific device
        # has its own learned correlations (AreaProfiles from button training).
        device_has_correlations = device.address in self.correlations and len(self.correlations[device.address]) > 0
        if (has_mature_profiles or device_has_correlations) and self._refresh_area_by_ukf(device):
            return
        self._refresh_area_by_min_distance(device)

    @dataclass
    class AreaTests:
        """
        Holds the results of Area-based tests.

        Likely to become a stand-alone class for performing the whole area-selection
        process.
        """

        device: str = ""
        scannername: tuple[str, str] = ("", "")
        areas: tuple[str, str] = ("", "")
        pcnt_diff: float = 0  # distance percentage difference.
        same_area: bool = False  # The old scanner is in the same area as us.
        # last_detection: tuple[float, float] = (0, 0)  # bt manager's last_detection field. Compare with ours.
        last_ad_age: tuple[float, float] = (0, 0)  # seconds since we last got *any* ad from scanner
        this_ad_age: tuple[float, float] = (0, 0)  # how old the *current* advert is on this scanner
        distance: tuple[float, float] = (0, 0)
        hist_min_max: tuple[float, float] = (0, 0)  # min/max distance from history
        floors: tuple[str | None, str | None] = (None, None)
        floor_levels: tuple[str | int | None, str | int | None] = (None, None)
        # velocity: tuple[float, float] = (0, 0)
        # last_closer: tuple[float, float] = (0, 0)  # since old was closer and how long new has been closer
        reason: str | None = None  # reason/result

        def sensortext(self) -> str:
            """Return a text summary suitable for use in a sensor entity."""
            out = ""
            for var, val in vars(self).items():
                out += f"{var}|"
                if isinstance(val, tuple):
                    for v in val:
                        if isinstance(v, float):
                            out += f"{v:.2f}|"
                        else:
                            out += f"{v}"
                    # out += "\n"
                elif var == "pcnt_diff":
                    out += f"{val:.3f}"
                else:
                    out += f"{val}"
                out += "\n"
            return out[:255]

        def __str__(self) -> str:
            """
            Create string representation for easy debug logging/dumping
            and potentially a sensor for logging Area decisions.
            """
            out = ""
            for var, val in vars(self).items():
                out += f"** {var:20} "
                if isinstance(val, tuple):
                    for v in val:
                        if isinstance(v, float):
                            out += f"{v:.2f} "
                        else:
                            out += f"{v} "
                    out += "\n"
                elif var == "pcnt_diff":
                    out += f"{val:.3f}\n"
                else:
                    out += f"{val}\n"
            return out

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

        tests = self.AreaTests()
        tests.device = device.name

        _superchatty = False  # Set to true for very verbose logging about area wins
        # if device.name in ("Ash Pixel IRK", "Garage", "Melinda iPhone"):
        #     _superchatty = True

        effective_cache: dict[BermudaAdvert, float | None] = {}

        def _effective_distance(advert: BermudaAdvert | None) -> float | None:
            """Return cached effective distance for an advert."""
            if advert is None:
                return None
            if isinstance(advert, Hashable):
                if advert not in effective_cache:
                    effective_cache[advert] = self.effective_distance(advert, nowstamp)
                return effective_cache[advert]
            return self.effective_distance(advert, nowstamp)

        def _belongs(advert: BermudaAdvert | None) -> bool:
            return advert is not None and advert in device.adverts.values()

        def _within_evidence(advert: BermudaAdvert | None) -> bool:
            return advert is not None and advert.stamp is not None and advert.stamp >= evidence_cutoff

        def _has_area(advert: BermudaAdvert | None) -> bool:
            return advert is not None and advert.area_id is not None

        def _area_candidate(advert: BermudaAdvert | None) -> bool:
            return _belongs(advert) and _has_area(advert)

        def _is_distance_contender(advert: BermudaAdvert | None) -> bool:
            effective_distance = _effective_distance(advert)
            return (
                _area_candidate(advert)
                and advert is not None
                and _within_evidence(advert)
                and effective_distance is not None
                and effective_distance <= _max_radius
            )

        has_distance_contender = any(_is_distance_contender(advert) for advert in device.adverts.values())

        # FIX: Weak Scanner Override Protection
        # If the device was previously in a scannerless UKF-detected area, protect it
        # from being overridden by distant scanners. Only allow switch if a scanner
        # is very close (< UKF_WEAK_SCANNER_MIN_DISTANCE) or UKF score has dropped.
        _protect_scannerless_area = getattr(device, "_ukf_scannerless_area", False)
        _scannerless_min_dist_override = UKF_WEAK_SCANNER_MIN_DISTANCE

        if not _is_distance_contender(incumbent):
            if _area_candidate(incumbent) and _within_evidence(incumbent):
                soft_incumbent = incumbent
            incumbent = None

        for challenger in device.adverts.values():
            # Check each scanner and any time one is found to be closer / better than
            # the existing closest_scanner, replace it. At the end we should have the
            # right one. In theory.
            #
            # Note that rssi_distance is smoothed/filtered, and might be None if the last
            # reading was old enough that our algo decides it's "away".
            #
            # Every loop, every test is just a two-way race.

            if not _within_evidence(challenger):
                continue

            # Is the challenger an invalid contender?
            if (
                # no competing against ourselves...
                (incumbent or soft_incumbent) is challenger  # no competing against ourselves.
            ):
                continue

            # No winning with stale adverts. If we didn't win back when it was fresh,
            # we've no business winning now. This guards against a single advert
            # being reported by two proxies at slightly different times, and the area
            # switching to the later one after the reading times out on the first.
            # The timeout value is fairly arbitrary, if it's too small then we risk
            # ignoring valid reports from slow proxies (or if our processing loop is
            # delayed / lengthened). Too long and we add needless jumping around for a
            # device that isn't actually being actively detected.
            if not _is_distance_contender(challenger):
                continue

            # At this point the challenger is a vaild contender...

            # Is the incumbent a valid contender?
            current_incumbent = incumbent or soft_incumbent
            incumbent_distance = _effective_distance(current_incumbent)
            if (
                incumbent_distance is None
                and current_incumbent is not None
                and current_incumbent is soft_incumbent
                and getattr(device, "area_advert", None) is soft_incumbent
                and getattr(device, "area_distance", None) is not None
                and _within_evidence(current_incumbent)
            ):
                incumbent_distance = device.area_distance
            challenger_scanner = challenger.scanner_device
            if challenger_scanner is None:
                tests.reason = "LOSS - challenger missing scanner metadata"
                continue

            incumbent_scanner = current_incumbent.scanner_device if current_incumbent else None
            inc_floor_level = getattr(incumbent_scanner, "floor_level", None) if incumbent_scanner else None

            # FIX: FEHLER 2 - Unified Floor Guard - ALWAYS resolve floor from device.area_id FIRST!
            #
            # The scanner_device belongs to a different room (and potentially different floor!),
            # so its floor_id is WRONG for determining cross-floor protection. This is critical
            # for "scannerless rooms" (Virtual Rooms) where the device is in a room without
            # its own scanner, but receives packets from scanners in adjacent rooms.
            #
            # Scenario fixed:
            # - Device is in "Virtual Room" (OG/Floor 1), heard by scanner "Kitchen" (EG/Floor 0)
            # - OLD CODE: inc_floor_id = scanner "Kitchen".floor_id = EG
            # - NEW CODE: inc_floor_id = device.area_id ("Virtual Room").floor_id = OG
            #
            # The device's CONFIRMED area (device.area_id) is the authoritative source of truth
            # for floor determination. Only fall back to scanner's floor when device.area_id is None.
            inc_floor_id: str | None = None

            # PRIMARY: Always try device.area_id first (authoritative source of truth)
            if device.area_id is not None:
                inc_floor_id = self._resolve_floor_id_for_area(device.area_id)

            # FALLBACK: Only if device has no confirmed area, use current incumbent's area or scanner
            if inc_floor_id is None and current_incumbent is not None:
                # Try incumbent advert's area_id
                current_inc_area_id = getattr(current_incumbent, "area_id", None)
                if current_inc_area_id is not None:
                    inc_floor_id = self._resolve_floor_id_for_area(current_inc_area_id)

            # LAST RESORT: Use scanner's floor (only when we have no area information at all)
            if inc_floor_id is None and incumbent_scanner is not None:
                inc_floor_id = getattr(incumbent_scanner, "floor_id", None)

            chal_floor_id = getattr(challenger_scanner, "floor_id", None)
            chal_floor_level = getattr(challenger_scanner, "floor_level", None)
            tests.floors = (inc_floor_id, chal_floor_id)
            tests.floor_levels = (inc_floor_level, chal_floor_level)
            cross_floor = inc_floor_id is not None and chal_floor_id is not None and inc_floor_id != chal_floor_id

            # FIX: Weak Scanner Override Protection for Scannerless Rooms
            # If device is in a scannerless UKF-detected area, don't let distant scanners override.
            # Only allow switch if challenger is very close OR we're on the same floor.
            if _protect_scannerless_area and current_incumbent is not None:
                challenger_dist = _effective_distance(challenger)
                if challenger_dist is not None and challenger_dist >= _scannerless_min_dist_override:
                    # Challenger is too far to override a scannerless area
                    if cross_floor:
                        # Cross-floor: definitely block distant scanners
                        tests.reason = (
                            f"LOSS - scannerless area protection (challenger at {challenger_dist:.1f}m "
                            f">= {_scannerless_min_dist_override:.1f}m, cross-floor)"
                        )
                        if _LOGGER.isEnabledFor(logging.DEBUG):
                            _LOGGER.debug(
                                "Weak scanner override blocked for %s: scannerless area protected, "
                                "challenger %s at %.1fm (min=%.1fm), cross-floor",
                                device.name,
                                challenger.name,
                                challenger_dist,
                                _scannerless_min_dist_override,
                            )
                        continue

            # If closest scanner lacks critical data, we win.
            if current_incumbent is None:
                # Default Instawin!
                incumbent = challenger
                soft_incumbent = None
                if _superchatty:
                    _LOGGER.debug(
                        "%s IS closesr to %s: Encumbant is invalid",
                        device.name,
                        challenger.name,
                    )
                continue

            # If the incumbent is a soft_incumbent (failed distance contention due to being
            # out of range or missing distance), a valid distance challenger wins immediately
            # without needing the history checks. This ensures out-of-range incumbents are
            # properly replaced by in-range challengers.
            # HOWEVER: For cross-floor switches, we still require minimum history to prevent
            # a device jumping to a different floor just because the current scanner went stale.
            # Bug Fix: Also check incumbent's history - if incumbent has substantial history
            # but challenger has minimal history, require challenger to prove itself longer.
            # NEW: Absolute profile rescue - if secondary scanner readings match learned profile
            # for current area, the device is likely still there even if primary scanner is offline.
            if current_incumbent is soft_incumbent and not _is_distance_contender(soft_incumbent):
                # ABSOLUTE PROFILE RESCUE: Check if secondary readings still match current area
                # This prevents room switches when the primary scanner temporarily goes offline
                # but other scanners still show the typical pattern for this area.
                if current_incumbent.area_id is not None:
                    current_area_id = current_incumbent.area_id
                    if device.address in self.correlations and current_area_id in self.correlations[device.address]:
                        profile = self.correlations[device.address][current_area_id]
                        # Gather RSSI readings from all visible scanners
                        all_readings: dict[str, float] = {}
                        for other_adv in device.adverts.values():
                            if (
                                _within_evidence(other_adv)
                                and other_adv.rssi is not None
                                and other_adv.scanner_address is not None
                            ):
                                all_readings[other_adv.scanner_address] = other_adv.rssi

                        # Check if we have enough mature absolute profiles to validate
                        if profile.mature_absolute_count >= 2:
                            z_scores = profile.get_absolute_z_scores(all_readings)
                            if len(z_scores) >= 2:
                                # Calculate average z-score (lower = better match)
                                avg_z = sum(z for _, z in z_scores) / len(z_scores)
                                # If readings match learned profile well (z < 2.0), protect area
                                if avg_z < 2.0:
                                    tests.reason = (
                                        f"LOSS - absolute profile match (z={avg_z:.2f}) protects current area"
                                    )
                                    if _superchatty:
                                        _LOGGER.debug(
                                            "%s: Absolute profile rescue - secondary readings match "
                                            "%s profile (avg_z=%.2f, scanners=%d)",
                                            device.name,
                                            current_area_id,
                                            avg_z,
                                            len(z_scores),
                                        )
                                    continue
                if cross_floor:
                    challenger_hist = challenger.hist_distance_by_interval
                    incumbent_hist = soft_incumbent.hist_distance_by_interval if soft_incumbent else []
                    # Require challenger to have minimum history
                    if len(challenger_hist) < CROSS_FLOOR_MIN_HISTORY:
                        tests.reason = "LOSS - soft incumbent but cross-floor history too short"
                        continue
                    # Bug Fix: If incumbent has substantial history (was recently valid),
                    # require challenger to have comparable or better history before winning.
                    # This prevents a new scanner with just 8 samples from beating a
                    # well-established incumbent that temporarily lost distance measurement.
                    if len(incumbent_hist) >= CROSS_FLOOR_MIN_HISTORY * 2:
                        # Incumbent was well-established - require challenger to have at least
                        # the same amount of history to prove it's consistently closer
                        if len(challenger_hist) < len(incumbent_hist) // 2:
                            tests.reason = "LOSS - soft incumbent has substantial history, challenger needs more"
                            continue
                else:
                    # FIX: Same-floor soft incumbent stabilization
                    # When incumbent scanner temporarily stops sending data ("soft incumbent"),
                    # don't let any challenger win immediately. This prevents room flickering
                    # when the current scanner has a brief dropout (common with BLE).
                    #
                    # IMPORTANT: Only apply stabilization when the incumbent WAS within range.
                    # If incumbent was OUT OF RANGE (beyond max_radius), let challenger win
                    # immediately - we shouldn't protect an invalid position.
                    #
                    # Require challenger to have EITHER:
                    # 1. Significantly better distance (> 0.5m closer) - clear physical proximity
                    # 2. Some sustained history (half of cross-floor requirement) - consistent readings
                    #
                    # This keeps the device in its current room during brief scanner outages,
                    # rather than jumping to whichever scanner happens to send data next.

                    # Get last known incumbent distance for comparison
                    soft_inc_distance = device.area_distance if device.area_distance is not None else None

                    # Only apply stabilization if incumbent was within valid range
                    # If incumbent was out of range (soft_inc_distance > max_radius or None),
                    # skip stabilization and let the valid challenger win
                    soft_inc_was_within_range = soft_inc_distance is not None and soft_inc_distance <= _max_radius

                    if soft_inc_was_within_range and soft_inc_distance is not None:
                        # Note: soft_inc_distance is guaranteed not None here due to the check above,
                        # but we add the explicit check for mypy's type narrowing.
                        challenger_hist = challenger.hist_distance_by_interval
                        challenger_dist = _effective_distance(challenger)
                        soft_inc_min_history = CROSS_FLOOR_MIN_HISTORY // 2  # 4 readings
                        soft_inc_min_distance_advantage = 0.5  # meters

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
                # No incumbent distance available; allow the challenger to compete to avoid deadlocks.
                # HOWEVER: For cross-floor switches, still require minimum history to prevent
                # jumping floors when the current scanner has no distance but hasn't timed out.
                if cross_floor:
                    challenger_hist = challenger.hist_distance_by_interval
                    if len(challenger_hist) < CROSS_FLOOR_MIN_HISTORY:
                        tests.reason = "LOSS - incumbent distance unavailable but cross-floor history too short"
                        continue
                tests.reason = "WIN - incumbent distance unavailable"
                incumbent = challenger
                soft_incumbent = None
                continue

            # NOTE:
            # From here on in, don't award a win directly. Instead award a loss if the new scanner is
            # not a contender, but otherwise build a set of test scores and make a determination at the
            # end.

            # If we ARE NOT ACTUALLY CLOSER(!) we can not win.
            challenger_distance = _effective_distance(challenger)
            if challenger_distance is None:
                continue

            # Physical RSSI Priority: Calculate RSSI advantage early for use in multiple checks
            challenger_rssi_advantage = 0.0
            if _use_physical_rssi_priority:
                challenger_median_rssi = challenger.median_rssi()
                incumbent_median_rssi = current_incumbent.median_rssi()
                if challenger_median_rssi is not None and incumbent_median_rssi is not None:
                    # Positive value means challenger has stronger physical signal
                    challenger_rssi_advantage = challenger_median_rssi - incumbent_median_rssi

            passed_via_rssi_override = False
            if incumbent_distance < challenger_distance:
                # Incumbent appears closer. Normally we would reject the challenger here.
                # BUT if RSSI priority is enabled and challenger has significantly stronger
                # physical signal, the incumbent's "closeness" may be due to offset gaming.
                if _use_physical_rssi_priority and challenger_rssi_advantage > RSSI_CONSISTENCY_MARGIN_DB:
                    # Challenger has much stronger signal despite appearing further on distance
                    # Allow it to proceed - the distance ranking may be wrong due to offsets
                    passed_via_rssi_override = True
                    if _superchatty:
                        _LOGGER.info(
                            "RSSI priority override: %s allowed despite further distance "
                            "(RSSI advantage %.1f dB > %.1f dB margin)",
                            challenger.name,
                            challenger_rssi_advantage,
                            RSSI_CONSISTENCY_MARGIN_DB,
                        )
                else:
                    # we are not even closer!
                    continue

            # STABILITY CHECK: Challenger must be SIGNIFICANTLY closer to challenge incumbent.
            # This prevents flickering when distances are nearly equal (e.g., 2.0m vs 2.1m).
            # The margin is DYNAMIC based on how long the device has been in the current area:
            # - MOVING (0-2 min): Lower threshold (5%) - easier to switch when moving
            # - SETTLING (2-10 min): Normal threshold (8%) - device is settling in
            # - STATIONARY (10+ min): Higher threshold (15%) - harder to switch when stationary
            # NOTE: Skip this check if:
            # - Challenger passed via RSSI override (distance already suspect due to offset gaming)
            # - Distances are essentially equal (let tie-breaking logic handle it)
            if not passed_via_rssi_override:
                distance_improvement = incumbent_distance - challenger_distance
                # Only apply stability margin if challenger is actually closer (not equal)
                # When distances are equal or challenger is further, other checks apply
                if distance_improvement > 0:
                    percent_improvement = distance_improvement / incumbent_distance if incumbent_distance > 0 else 0

                    # Dynamic margin based on dwell time
                    movement_state = device.get_movement_state(stamp_now=nowstamp)
                    if movement_state == MOVEMENT_STATE_MOVING:
                        required_percent = MARGIN_MOVING_PERCENT
                        required_meters = INCUMBENT_MARGIN_METERS  # Keep base meters threshold
                    elif movement_state == MOVEMENT_STATE_SETTLING:
                        required_percent = MARGIN_SETTLING_PERCENT
                        required_meters = INCUMBENT_MARGIN_METERS
                    else:  # STATIONARY
                        required_percent = MARGIN_STATIONARY_PERCENT
                        required_meters = MARGIN_STATIONARY_METERS

                    # Must meet either the percentage OR absolute threshold (whichever is easier)
                    meets_stability_margin = (
                        percent_improvement >= required_percent or distance_improvement >= required_meters
                    )
                    if not meets_stability_margin:
                        # Challenger is closer but not significantly - incumbent keeps advantage
                        if _superchatty:
                            _LOGGER.debug(
                                "Stability margin (%s): %s rejected (%.2fm, %.1f%% < %.1f%% or %.2fm)",
                                movement_state,
                                challenger.name,
                                distance_improvement,
                                percent_improvement * 100,
                                required_percent * 100,
                                required_meters,
                            )
                        continue

            # Physical RSSI Priority Check: If challenger appears closer on distance but
            # has significantly weaker physical signal, it may be winning only due to offset.
            if _use_physical_rssi_priority and challenger_rssi_advantage < -RSSI_CONSISTENCY_MARGIN_DB:
                # Challenger is "closer" on distance but has much weaker physical signal
                # This indicates the distance advantage is likely from offset, not proximity
                if _superchatty:
                    _LOGGER.info(
                        "RSSI consistency check: %s rejected (RSSI disadvantage %.1f dB > %.1f dB margin)",
                        challenger.name,
                        -challenger_rssi_advantage,
                        RSSI_CONSISTENCY_MARGIN_DB,
                    )
                continue

            tests.reason = None  # ensure we don't trigger logging if no decision was made.
            tests.same_area = current_incumbent.area_id == challenger.area_id
            tests.areas = (current_incumbent.area_name or "", challenger.area_name or "")
            tests.scannername = (current_incumbent.name, challenger.name)
            tests.distance = (incumbent_distance, challenger_distance)
            # tests.velocity = (
            #     next((val for val in closest_scanner.hist_velocity), 0),
            #     next((val for val in scanner.hist_velocity), 0),
            # )

            # How recently have we heard from the scanners themselves (not just for this device's adverts)?
            tests.last_ad_age = (
                nowstamp - incumbent_scanner.last_seen,
                nowstamp - challenger_scanner.last_seen,
            )

            # How old are the ads?
            tests.this_ad_age = (
                nowstamp - current_incumbent.stamp,
                nowstamp - challenger.stamp,
            )

            # Calculate the percentage difference between the challenger and incumbent's distances
            _pda = challenger_distance
            _pdb = incumbent_distance
            tests.pcnt_diff = abs(_pda - _pdb) / ((_pda + _pdb) / 2)
            abs_diff = abs(_pda - _pdb)
            avg_dist = (_pda + _pdb) / 2
            cross_floor_margin = 0.25
            cross_floor_escape = 0.45
            history_window = 5  # the time period to compare between us and incumbent
            cross_floor_min_history = CROSS_FLOOR_MIN_HISTORY  # Require longer history before cross-floor wins

            # Same-Floor-Confirmation: Count how many scanners on each floor see this device.
            # If multiple scanners on the current floor see it, require stronger evidence
            # for a cross-floor switch. Also penalize when challenger's floor has many more
            # scanners to prevent "gravitational pull" from multiple distant scanners.
            if cross_floor and inc_floor_id is not None:
                incumbent_floor_witnesses = 0
                challenger_floor_witnesses = 0
                challenger_floor_distances: list[float] = []
                # Collect floor levels from all contending scanners for sandwich logic
                witness_floor_levels: set[int] = set()
                for witness_adv in device.adverts.values():
                    if not _is_distance_contender(witness_adv):
                        continue
                    witness_scanner = witness_adv.scanner_device
                    if witness_scanner is None:
                        continue
                    witness_floor = getattr(witness_scanner, "floor_id", None)
                    witness_level = getattr(witness_scanner, "floor_level", None)
                    witness_dist = _effective_distance(witness_adv)
                    if witness_floor == inc_floor_id:
                        incumbent_floor_witnesses += 1
                    if witness_floor == chal_floor_id:
                        challenger_floor_witnesses += 1
                        if witness_dist is not None:
                            challenger_floor_distances.append(witness_dist)
                    if isinstance(witness_level, int):
                        witness_floor_levels.add(witness_level)

                # If 2+ scanners on the current floor see the device, increase thresholds
                if incumbent_floor_witnesses >= 2:
                    # Each additional witness increases the required margin by 10%
                    extra_margin = 0.10 * (incumbent_floor_witnesses - 1)
                    cross_floor_margin = min(0.60, cross_floor_margin + extra_margin)
                    cross_floor_escape = min(0.80, cross_floor_escape + extra_margin)

                # Challenger-Floor-Penalty: If the challenger's floor has significantly more
                # scanners than the incumbent's floor, require stronger evidence. This prevents
                # multiple distant scanners from "pulling" a device away from a single close scanner.
                # Example: 1 scanner on EG (2m away) vs 3 scanners on OG (further away) should
                # not result in a switch just because OG has more scanners voting for it.
                if challenger_floor_witnesses > incumbent_floor_witnesses:
                    witness_imbalance = challenger_floor_witnesses - incumbent_floor_witnesses
                    # Add 15% margin per extra challenger floor witness
                    imbalance_margin = 0.15 * witness_imbalance
                    cross_floor_margin = min(0.70, cross_floor_margin + imbalance_margin)
                    cross_floor_escape = min(0.85, cross_floor_escape + imbalance_margin)

                # Distance-Weighted Near-Field Protection: If incumbent is very close (near-field)
                # and challenger floor witnesses are significantly further away, add strong protection.
                # Physical reasoning: BLE signal follows inverse-square law. A device 2m away has
                # ~4x stronger signal than one 4m away. Multiple distant scanners shouldn't override
                # a single close scanner just by "voting together".
                near_field_threshold = 3.0  # meters - considered "very close"
                if incumbent_distance <= near_field_threshold and challenger_floor_distances:
                    # Calculate minimum distance on challenger floor (best case for challenger)
                    min_challenger_dist = min(challenger_floor_distances)
                    # If even the closest challenger witness is significantly further than incumbent,
                    # add extra protection proportional to the distance ratio
                    if min_challenger_dist > incumbent_distance:
                        distance_ratio = min_challenger_dist / incumbent_distance
                        # If ratio >= 2.0 (challenger at least 2x further), add strong protection
                        # Scale: ratio 1.5 = +10%, ratio 2.0 = +20%, ratio 3.0 = +40%
                        if distance_ratio >= 1.5:
                            ratio_margin = 0.20 * (distance_ratio - 1.0)
                            cross_floor_margin = min(0.80, cross_floor_margin + ratio_margin)
                            cross_floor_escape = min(0.95, cross_floor_escape + ratio_margin)

                # Floor-Sandwich Logic: If the incumbent floor is "sandwiched" between
                # floors that also see the device, it's very likely the device is actually
                # on the incumbent floor. Example: If KG (-1), EG (0), and OG (1) all see
                # the device and incumbent is EG (0), then EG is most probable.
                if isinstance(inc_floor_level, int) and len(witness_floor_levels) >= 2:
                    levels_below = [lvl for lvl in witness_floor_levels if lvl < inc_floor_level]
                    levels_above = [lvl for lvl in witness_floor_levels if lvl > inc_floor_level]

                    # Check if incumbent floor is sandwiched (floors both above AND below see device)
                    is_sandwiched = bool(levels_below) and bool(levels_above)

                    if is_sandwiched:
                        # Strong evidence that device is on the middle floor
                        # Add significant margin boost (30% base + 5% per extra sandwiching floor)
                        sandwich_floors = len(levels_below) + len(levels_above)
                        sandwich_margin = 0.30 + 0.05 * (sandwich_floors - 2)
                        cross_floor_margin = min(0.75, cross_floor_margin + sandwich_margin)
                        cross_floor_escape = min(0.90, cross_floor_escape + sandwich_margin)

                    # Adjacent-floor bonus: If challenger is NOT adjacent to incumbent,
                    # require even stronger evidence (BLE rarely skips floors cleanly)
                    if isinstance(chal_floor_level, int):
                        floor_distance = abs(chal_floor_level - inc_floor_level)
                        if floor_distance >= 2:
                            # Non-adjacent floors: add 35% per floor gap beyond 1
                            skip_margin = 0.35 * (floor_distance - 1)
                            cross_floor_margin = min(0.80, cross_floor_margin + skip_margin)
                            cross_floor_escape = min(0.95, cross_floor_escape + skip_margin)

            # Same area. Confirm freshness and distance.
            if (
                tests.same_area
                and (tests.this_ad_age[0] > tests.this_ad_age[1] + 1)
                and tests.distance[0] >= tests.distance[1]
            ):
                tests.reason = "WIN awarded for same area, newer, closer advert"
                incumbent = challenger
                continue

            # Hysteresis.
            # If our worst reading in max_seconds is still closer than the incumbent's **best** reading
            # in that time, and we are over a PD threshold, we win.
            #
            min_history = 3  # we must have at least this much history
            pdiff_outright = 0.30  # Percentage difference to win outright / instantly
            pdiff_historical = 0.15  # Percentage difference required to win on historical test
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
            if len(challenger.hist_distance_by_interval) > min_history:  # we have enough history, let's go..
                if incumbent_history and challenger_history:
                    # The closest that the incumbent has been, vs the furthest we have been in that time window
                    tests.hist_min_max = (
                        min(incumbent_history),
                        max(challenger_history),
                    )
                    # For cross-floor switches, require the increased margin; otherwise use pdiff_historical
                    hist_margin = cross_floor_margin if cross_floor else pdiff_historical
                    if (
                        tests.hist_min_max[1] < tests.hist_min_max[0]
                        and tests.pcnt_diff > hist_margin  # and we're significantly closer.
                    ):
                        tests.reason = "WIN on historical min/max"
                        incumbent = challenger
                        continue

            # Check for near-field absolute improvement BEFORE applying history requirement.
            # This allows meaningful absolute improvements in close proximity to win
            # even without extensive history.
            near_field_cutoff = 1.0
            abs_win_meters = 0.08
            near_field_win = avg_dist <= near_field_cutoff and abs_diff >= abs_win_meters

            # Check if percentage difference meets the "outright win" threshold.
            # If so, bypass history requirement since the evidence is strong.
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
                # FIX: Fehler 3 - Cross-floor switches MUST have sustained evidence.
                # Previously, a high percentage improvement (cross_floor_escape ~45-95%)
                # could bypass the history requirement entirely, causing rapid floor
                # flickering with BLE signal reflections. Now we require BOTH:
                # 1. Either sustained_cross_floor (proper history evidence), OR
                # 2. A very high escape threshold (>100%) AND minimum history
                # This prevents "instant jumps" based on momentary signal spikes.
                min_cross_floor_history = max(history_window, cross_floor_min_history // 2)
                has_minimum_history = (
                    len(challenger_history) >= min_cross_floor_history
                    and len(incumbent_history) >= min_cross_floor_history
                )
                # FIX: Raise escape threshold to 100% (double the distance difference)
                # and require at least some history even for escape
                cross_floor_escape_strict = max(cross_floor_escape, 1.0)
                escape_with_history = tests.pcnt_diff >= cross_floor_escape_strict and has_minimum_history
                if not (sustained_cross_floor or escape_with_history):
                    tests.reason = "LOSS - cross-floor evidence insufficient"
                    continue
            # Same-floor: require minimum history before allowing a win.
            # This prevents a scanner with little/no history from immediately
            # "winning" just because it reports a closer distance.
            # Exceptions:
            # - near-field absolute improvements can bypass this
            # - significant percentage improvements (>30%) can bypass this
            # - RSSI tie-break for equal distances (when feature enabled)
            # RSSI-based wins: tie-break for equal distances, or significant advantage
            rssi_tiebreak_win = False
            rssi_advantage_win = False
            if _use_physical_rssi_priority:
                challenger_median = challenger.median_rssi()
                incumbent_median = current_incumbent.median_rssi()
                if challenger_median is not None and incumbent_median is not None:
                    rssi_advantage = challenger_median - incumbent_median
                    # Tie-break when distances are essentially equal
                    if abs_diff < 0.01 and rssi_advantage > 0:
                        rssi_tiebreak_win = True
                    # Significant RSSI advantage wins even if distance ranking differs
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
                # Allow a near-field absolute improvement to win even when percent diff is small.
                if not near_field_win:
                    # RSSI wins: strong RSSI advantage or tie-break
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

            # If we made it through all of that, we're winning, so far!
            tests.reason = "WIN by not losing!"

            incumbent = challenger
            soft_incumbent = None

        if _superchatty and tests.reason is not None:
            _LOGGER.info(
                "***************\n**************** %s *******************\n%s",
                tests.reason,
                tests,
            )

        _superchatty = False

        rssi_fallback_margin = 3.0
        rssi_fallback_cross_floor_margin = 6.0  # Bug Fix: Higher margin for cross-floor RSSI switches
        winner = incumbent or soft_incumbent

        # Virtual Distance for Scannerless Rooms
        # When a device has been button-trained for a scannerless room, we calculate a
        # "virtual distance" based on how well the current RSSI pattern matches the trained
        # fingerprint. This allows scannerless rooms to compete with physical scanner-based
        # distance measurements. Without this, scannerless rooms are invisible to min-distance
        # and can never be selected as the winner.
        virtual_winner_area_id: str | None = None
        virtual_winner_distance: float | None = None

        # Collect fresh RSSI readings for virtual distance calculation
        rssi_readings_for_virtual: dict[str, float] = {}
        for adv in device.adverts.values():
            if _within_evidence(adv) and adv.rssi is not None and adv.scanner_address is not None:
                rssi_readings_for_virtual[adv.scanner_address] = adv.rssi

        if rssi_readings_for_virtual:
            virtual_distances = self._get_virtual_distances_for_scannerless_rooms(device, rssi_readings_for_virtual)

            if virtual_distances:
                # Find the best virtual candidate (shortest virtual distance)
                best_virtual_area = min(
                    virtual_distances.keys(),
                    key=lambda area: virtual_distances[area],
                )
                best_virtual_dist = virtual_distances[best_virtual_area]

                # Compare against physical winner
                winner_distance = _effective_distance(winner) if winner else None

                # Virtual room wins if:
                # 1. No physical winner exists, OR
                # 2. Virtual distance is shorter than physical distance
                if winner is None or winner_distance is None:
                    # No physical winner - virtual room takes over
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
                    # Virtual room beats physical scanner
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
            # Apply the scannerless room as the winner
            device.update_area_and_floor(virtual_winner_area_id)

            # Set area_distance to the virtual distance for UI display
            # Note: This is a "virtual" distance based on fingerprint match, not physical
            device.area_distance = virtual_winner_distance
            device.area_distance_stamp = nowstamp

            # Mark this as a UKF-detected scannerless area for stickiness protection
            device._ukf_scannerless_area = True  # type: ignore[attr-defined]  # noqa: SLF001

            # Clear pending state since we've made a decision
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0

            tests.reason = f"WIN via virtual distance ({virtual_winner_distance:.2f}m) for scannerless room"
            device.diag_area_switch = tests.sensortext()

            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Applied virtual distance winner for %s: area=%s distance=%.2fm",
                    device.name,
                    virtual_winner_area_id,
                    virtual_winner_distance if virtual_winner_distance else 0,
                )
            return

        if not has_distance_contender:

            def _evidence_ok(advert: BermudaAdvert | None) -> bool:
                return _within_evidence(advert)

            def _get_floor_id(advert: BermudaAdvert | None) -> str | None:
                """Get floor_id from advert's scanner device."""
                if advert is None or advert.scanner_device is None:
                    return None
                return getattr(advert.scanner_device, "floor_id", None)

            fallback_candidates: list[BermudaAdvert] = []
            for adv in device.adverts.values():
                if not _area_candidate(adv) or not _evidence_ok(adv):
                    continue
                adv_effective = _effective_distance(adv)
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
                    if _area_candidate(device.area_advert) and _evidence_ok(device.area_advert)
                    else None
                )
                best_rssi = best_by_rssi.rssi
                incumbent_rssi = incumbent_candidate.rssi if incumbent_candidate is not None else None

                # Bug Fix: Check if this is a cross-floor switch and apply stricter margin
                rssi_is_cross_floor = False
                if incumbent_candidate is not None and best_by_rssi is not incumbent_candidate:
                    inc_floor = _get_floor_id(incumbent_candidate)
                    best_floor = _get_floor_id(best_by_rssi)
                    rssi_is_cross_floor = inc_floor is not None and best_floor is not None and inc_floor != best_floor

                # Use higher margin for cross-floor RSSI switches
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
                    # Bug Fix: For cross-floor RSSI switches, don't apply immediately -
                    # let the streak logic handle it to provide stability
                    if rssi_is_cross_floor:
                        # Don't set winner here - let streak logic below handle the switch
                        # This ensures cross-floor RSSI switches also require confirmation
                        winner = incumbent_candidate  # Keep incumbent, challenger goes through streak
                        tests.reason = "HOLD via RSSI cross-floor protection (needs streak confirmation)"
                    else:
                        winner = best_by_rssi
                        tests.reason = "WIN via RSSI fallback margin"
                else:
                    winner = incumbent_candidate
                    tests.reason = "HOLD via RSSI fallback hysteresis"

                # Populate diagnostic fields for RSSI fallback path so the diagnostic
                # shows meaningful scanner information even when no distance contenders exist.
                # This fixes the "scannername|" empty diagnostic for dual-stack devices.
                winner_name = ""
                winner_area = ""
                winner_distance = 0.0
                if winner is not None:
                    winner_name = getattr(winner, "name", "") or ""
                    winner_area = getattr(winner, "area_name", "") or ""
                    winner_distance = _effective_distance(winner) or 0.0
                incumbent_name = ""
                incumbent_area = ""
                incumbent_dist = 0.0
                if incumbent_candidate is not None and incumbent_candidate is not winner:
                    incumbent_name = getattr(incumbent_candidate, "name", "") or ""
                    incumbent_area = getattr(incumbent_candidate, "area_name", "") or ""
                    incumbent_dist = _effective_distance(incumbent_candidate) or 0.0
                tests.scannername = (incumbent_name, winner_name)
                tests.areas = (incumbent_area, winner_area)
                tests.distance = (incumbent_dist, winner_distance)
            else:
                winner = None

        if device.area_advert != winner and tests.reason is not None:
            device.diag_area_switch = tests.sensortext()

        # Apply the newly-found closest scanner (or apply None if we didn't find one)
        def _resolve_cross_floor(current: BermudaAdvert | None, candidate: BermudaAdvert | None) -> bool:
            cur_floor = getattr(current.scanner_device, "floor_id", None) if current else None
            cand_floor = getattr(candidate.scanner_device, "floor_id", None) if candidate else None
            return cur_floor is not None and cand_floor is not None and cur_floor != cand_floor

        def _get_visible_scanners() -> set[str]:
            """Get addresses of all scanners currently seeing this device."""
            visible = set()
            for adv in device.adverts.values():
                if _is_distance_contender(adv) and adv.scanner_device is not None:
                    visible.add(adv.scanner_device.address)
            return visible

        def _get_all_known_scanners_for_area(area_id: str) -> set[str]:
            """Get all scanner addresses that have ever seen this device in this area."""
            if area_id not in device.co_visibility_stats:
                return set()
            return set(device.co_visibility_stats[area_id].keys())

        def _apply_selection(advert: BermudaAdvert | None) -> None:
            device.apply_scanner_selection(advert, nowstamp=nowstamp)

            # Update co-visibility statistics when applying a valid selection
            if advert is not None and advert.area_id is not None:
                visible_scanners = _get_visible_scanners()
                # Include current visible scanners in the set of known scanners for this area
                known_scanners = _get_all_known_scanners_for_area(advert.area_id)
                all_candidate_scanners = known_scanners | visible_scanners
                device.update_co_visibility(advert.area_id, visible_scanners, all_candidate_scanners)

                # Update scanner correlations - learn RSSI relationships for this area
                if advert.rssi is not None:
                    other_readings: dict[str, float] = {}
                    for other_adv in device.adverts.values():
                        if (
                            other_adv is not advert
                            and _within_evidence(other_adv)
                            and other_adv.rssi is not None
                            and other_adv.scanner_address is not None
                        ):
                            other_readings[other_adv.scanner_address] = other_adv.rssi

                    if other_readings:
                        # Ensure device entry exists in correlations
                        if device.address not in self.correlations:
                            self.correlations[device.address] = {}
                        # Ensure area entry exists for this device
                        if advert.area_id not in self.correlations[device.address]:
                            self.correlations[device.address][advert.area_id] = AreaProfile(
                                area_id=advert.area_id,
                            )
                        # Update the device-specific profile
                        self.correlations[device.address][advert.area_id].update(
                            primary_rssi=advert.rssi,
                            other_readings=other_readings,
                            primary_scanner_addr=advert.scanner_address,
                        )

                        # Also update the device-independent room profile
                        # Collect all RSSI readings for this update
                        all_readings = dict(other_readings)
                        if advert.scanner_address is not None:
                            all_readings[advert.scanner_address] = advert.rssi
                        if advert.area_id not in self.room_profiles:
                            self.room_profiles[advert.area_id] = RoomProfile(area_id=advert.area_id)
                        self.room_profiles[advert.area_id].update(all_readings)

        if winner is None:
            candidates: list[tuple[float, BermudaAdvert]] = []
            for adv in device.adverts.values():
                if not (_has_area(adv) and _within_evidence(adv)):
                    continue
                adv_effective = _effective_distance(adv)
                if adv_effective is None or adv_effective > _max_radius:
                    continue
                candidates.append((adv_effective, adv))
            if candidates:
                if _use_physical_rssi_priority:
                    # Tie-break by raw RSSI (stronger signal = more likely to be physically closer)
                    # Negative because higher RSSI is better, but min() selects lowest
                    def _rssi_sort_key(item: tuple[float, BermudaAdvert]) -> tuple[float, float]:
                        rssi = item[1].median_rssi()
                        return (item[0], -(rssi if rssi is not None else float("-inf")))

                    winner = min(candidates, key=_rssi_sort_key)[1]
                else:
                    # Original behavior: tie-break by timestamp
                    winner = min(
                        candidates,
                        key=lambda item: (item[0], item[1].stamp if item[1].stamp is not None else 0),
                    )[1]
                tests.reason = "WIN via rescue candidate"
            if winner is not None:
                _apply_selection(winner)
                return

            if _LOGGER.isEnabledFor(logging.DEBUG):
                fresh_adverts = [adv for adv in device.adverts.values() if _within_evidence(adv)]
                fresh_with_area = [adv for adv in fresh_adverts if _has_area(adv)]
                with_effective = [adv for adv in fresh_with_area if _effective_distance(adv) is not None]
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
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            _apply_selection(None)
            return

        if device.area_advert is winner:
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            _apply_selection(winner)
            return

        cross_floor = _resolve_cross_floor(device.area_advert, winner)
        streak_target = CROSS_FLOOR_STREAK if cross_floor else SAME_FLOOR_STREAK

        # Bug Fix: Calculate confidence scores ALWAYS, not just when area differs.
        # These scores are used both for streak_target adjustment AND for streak validation.
        # Previously, confidence was only checked when winner.area_id != incumbent.area_id,
        # which meant the streak could build up before confidence was ever evaluated.
        winner_confidence = 1.0
        incumbent_confidence = 1.0
        winner_corr_confidence = 1.0
        incumbent_corr_confidence = 1.0
        low_confidence_winner = False

        if winner is not None and winner.area_id is not None:
            visible_scanners = _get_visible_scanners()
            winner_confidence = device.get_co_visibility_confidence(winner.area_id, visible_scanners)

            # Scanner Correlation Confidence Check
            current_readings: dict[str, float] = {
                adv.scanner_address: adv.rssi
                for adv in device.adverts.values()
                if _within_evidence(adv) and adv.rssi is not None and adv.scanner_address is not None
            }
            winner_corr_confidence = self._get_correlation_confidence(
                device.address, winner.area_id, winner.rssi, current_readings
            )

            if device.area_advert is not None and device.area_advert.area_id is not None:
                incumbent_confidence = device.get_co_visibility_confidence(device.area_advert.area_id, visible_scanners)
                incumbent_corr_confidence = self._get_correlation_confidence(
                    device.address, device.area_advert.area_id, device.area_advert.rssi, current_readings
                )

            # Check if winner has suspiciously low confidence
            if winner.area_id != (device.area_advert.area_id if device.area_advert else None):
                # Co-Visibility Confidence Check: If the winner's area has low co-visibility
                # confidence (expected scanners are missing) but the incumbent has high
                # confidence, prefer the incumbent.
                if winner_confidence < 0.7 and incumbent_confidence > winner_confidence + 0.2:
                    # Double the streak requirement when co-visibility is suspicious
                    streak_target = max(streak_target, streak_target * 2)
                    low_confidence_winner = True

                # Correlation confidence check
                if winner_corr_confidence < 0.5 and incumbent_corr_confidence > winner_corr_confidence + 0.3:
                    streak_target = max(streak_target, streak_target * 2)
                    low_confidence_winner = True

        if device.area_advert is None and winner is not None:
            # Bootstrap immediately when we have no area yet; don't wait for streak logic.
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            _apply_selection(winner)
            return

        # Also bootstrap immediately when the current area_advert is out of range, invalid,
        # stale, or when there's a significant improvement on the SAME floor.
        # Cross-floor switches still require the streak for stability.
        area_advert_stale = False
        significant_improvement_same_floor = False
        significant_rssi_advantage = False
        if device.area_advert is not None:
            max_age = getattr(device.area_advert, "adaptive_timeout", None) or AREA_MAX_AD_AGE_DEFAULT
            max_age = min(max_age, AREA_MAX_AD_AGE_LIMIT)
            area_advert_stale = device.area_advert.stamp < nowstamp - max_age
            # Check for significant improvement (>30% distance difference) on same floor only
            if winner is not None:
                streak_winner_dist = _effective_distance(winner)
                streak_incumbent_dist = _effective_distance(device.area_advert)
                is_cross_floor = _resolve_cross_floor(device.area_advert, winner)
                if (
                    streak_winner_dist is not None
                    and streak_incumbent_dist is not None
                    and streak_winner_dist < streak_incumbent_dist
                    and not is_cross_floor  # Only apply for same-floor switches
                ):
                    streak_avg = (streak_winner_dist + streak_incumbent_dist) / 2
                    streak_pcnt = abs(streak_winner_dist - streak_incumbent_dist) / streak_avg if streak_avg > 0 else 0
                    significant_improvement_same_floor = streak_pcnt >= 0.30  # pdiff_outright threshold

                # RSSI Priority: Significant RSSI advantage also counts as strong evidence
                # and should bypass streak requirement on same floor
                if _use_physical_rssi_priority and not is_cross_floor:
                    winner_rssi = winner.median_rssi() if hasattr(winner, "median_rssi") else None
                    incumbent_rssi = (
                        device.area_advert.median_rssi() if hasattr(device.area_advert, "median_rssi") else None
                    )
                    if winner_rssi is not None and incumbent_rssi is not None:
                        rssi_advantage = winner_rssi - incumbent_rssi
                        if rssi_advantage > RSSI_CONSISTENCY_MARGIN_DB:
                            significant_rssi_advantage = True

        # Bug Fix: Bootstrap exceptions should NOT bypass cross-floor protection.
        # Only allow immediate switch if:
        # 1. It's NOT a cross-floor switch, OR
        # 2. The incumbent is truly invalid (stale or not a distance contender)
        # Previously, significant_improvement_same_floor and significant_rssi_advantage
        # could trigger immediate switches even for cross-floor cases due to the OR logic.
        #
        # Bug Fix 2: Cross-floor switches should STILL require streak even if incumbent
        # is "truly invalid" (just out of range). Only bypass streak if incumbent is
        # COMPLETELY offline (no advert at all, or advert is completely stale).
        # This prevents rapid flickering between floors when a device is in a room
        # without its own scanner and all scanners report distances near max_radius.
        is_cross_floor_switch = _resolve_cross_floor(device.area_advert, winner)
        incumbent_truly_invalid = not _is_distance_contender(device.area_advert) or area_advert_stale
        same_floor_fast_track = not is_cross_floor_switch and (
            significant_improvement_same_floor or significant_rssi_advantage
        )

        # For cross-floor switches, require the incumbent to be COMPLETELY offline
        # (not just out of range) before allowing an immediate switch.
        incumbent_completely_offline = (
            device.area_advert is None
            or device.area_advert.stamp is None
            or device.area_advert.stamp < nowstamp - AREA_MAX_AD_AGE_LIMIT
        )

        # Cross-floor: only bypass streak if incumbent is completely offline
        # Same-floor: allow bypass if incumbent is just invalid OR significant improvement
        allow_immediate_switch = winner is not None and (
            (is_cross_floor_switch and incumbent_completely_offline)
            or (not is_cross_floor_switch and (incumbent_truly_invalid or same_floor_fast_track))
        )

        if allow_immediate_switch:
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            _apply_selection(winner)
            return

        # Bug Fix: Improved streak logic for multi-room fluctuations.
        # When a device fluctuates between multiple rooms (A→B→C→A), the streak was
        # always reset to 1, preventing any room from reaching the threshold.
        # New logic: If the winner matches the pending candidate, increment streak.
        # If winner matches current area_advert, reset pending state (device is stable).
        # If winner is a NEW third candidate, only reset if it's more promising.
        winner_floor_id = getattr(winner.scanner_device, "floor_id", None)

        if device.pending_area_id == winner.area_id and device.pending_floor_id == winner_floor_id:
            # Winner matches pending candidate - increment streak
            # Bug Fix: Only increment if winner has acceptable confidence.
            # If confidence is very low, don't count this cycle toward the streak.
            # This prevents accumulating streak during intermittent false readings.
            if low_confidence_winner and winner_confidence < 0.5:
                # Very low confidence - don't increment streak this cycle
                pass
            else:
                device.pending_streak += 1
        elif device.pending_area_id is not None and device.pending_area_id != winner.area_id:
            # A different candidate appeared - check if we should switch candidates
            # Only switch if the new candidate is significantly closer than our pending one
            pending_advert = next(
                (adv for adv in device.adverts.values() if adv.area_id == device.pending_area_id),
                None,
            )
            pending_dist = _effective_distance(pending_advert) if pending_advert else None
            winner_dist = _effective_distance(winner)

            # If winner is significantly closer (>20% improvement), switch to new candidate
            # Otherwise, don't reset - keep building streak for the pending candidate
            if pending_dist is not None and winner_dist is not None:
                improvement = (pending_dist - winner_dist) / pending_dist if pending_dist > 0 else 0
                if improvement > 0.20:
                    # Significant improvement - switch to new candidate
                    device.pending_area_id = winner.area_id
                    device.pending_floor_id = winner_floor_id
                    device.pending_streak = 1
                # else: keep current pending candidate, don't increment streak
            else:
                # No valid distance comparison - reset to new candidate
                device.pending_area_id = winner.area_id
                device.pending_floor_id = winner_floor_id
                device.pending_streak = 1
        else:
            # First pending candidate or same floor different area
            device.pending_area_id = winner.area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1

        if device.pending_streak >= streak_target:
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            _apply_selection(winner)
        else:
            device.diag_area_switch = tests.sensortext()
            _apply_selection(device.area_advert)

    def _refresh_scanners(self, force=False):
        """
        Refresh data on existing scanner objects, and rebuild if scannerlist has changed.

        Called on every update cycle, this handles the *fast* updates (such as updating
        timestamps). If it detects that the list of scanners has changed (or is called
        with force=True) then the full list of scanners will be rebuild by calling
        _rebuild_scanners.
        """
        self._rebuild_scanner_list(force=force)

    def _rebuild_scanner_list(self, force=False):
        """
        Rebuild Bermuda's internal list of scanners.

        Called on every update (via _refresh_scanners) but exits *quickly*
        *unless*:
          - the scanner set has changed or
          - force=True or
          - self._force_full_scanner_init=True
        """
        # Using new API in 2025.2
        _new_ha_scanners: set[BaseHaScanner] = set(self._manager.async_current_scanners())

        if _new_ha_scanners is self._hascanners or _new_ha_scanners == self._hascanners:
            # No changes.
            return

        _LOGGER.debug("HA Base Scanner Set has changed, rebuilding.")
        self._hascanners = _new_ha_scanners

        self._async_purge_removed_scanners()

        # So we can raise a single repair listing all area-less scanners:
        _scanners_without_areas: list[str] = []

        # Find active HaBaseScanners in the backend and treat that as our
        # authoritative source of truth.
        #
        for hascanner in self._hascanners:
            scanner_address = normalize_address(hascanner.source)
            bermuda_scanner = self._get_or_create_device(scanner_address)
            bermuda_scanner.async_as_scanner_init(hascanner)

            if bermuda_scanner.area_id is None:
                _scanners_without_areas.append(f"{bermuda_scanner.name} [{bermuda_scanner.address}]")
        self._async_manage_repair_scanners_without_areas(_scanners_without_areas)

    def _async_purge_removed_scanners(self):
        """Demotes any devices that are no longer scanners based on new self.hascanners."""
        _scanners = [device.address for device in self.devices.values() if device.is_scanner]
        for ha_scanner in self._hascanners:
            scanner_address = normalize_address(ha_scanner.source)
            if scanner_address in _scanners:
                # This is still an extant HA Scanner, so we'll keep it.
                _scanners.remove(scanner_address)
        # Whatever's left are presumably no longer scanners.
        for address in _scanners:
            _LOGGER.info("Demoting ex-scanner %s", self.devices[address].name)
            self.devices[address].async_as_scanner_nolonger()

    def _async_manage_repair_scanners_without_areas(self, scannerlist: list[str]) -> None:
        """
        Raise a repair for any scanners that lack an area assignment.

        This function will take care of ensuring a repair is (re)raised
        or cleared (if the list is empty) when given a list of area-less scanner names.

        scannerlist should contain a friendly string to name each scanner missing an area.
        """
        if self._scanners_without_areas != scannerlist:
            self._scanners_without_areas = scannerlist
            # Clear any existing repair, because it's either resolved now (empty list) or we need to re-issue
            # the repair in order to update the scanner list (re-calling doesn't update it).
            ir.async_delete_issue(self.hass, DOMAIN, REPAIR_SCANNER_WITHOUT_AREA)

            if self._scanners_without_areas and len(self._scanners_without_areas) != 0:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    REPAIR_SCANNER_WITHOUT_AREA,
                    translation_key=REPAIR_SCANNER_WITHOUT_AREA,
                    translation_placeholders={
                        "scannerlist": "".join(f"- {name}\n" for name in self._scanners_without_areas),
                    },
                    severity=ir.IssueSeverity.ERROR,
                    is_fixable=False,
                )

    # *** Not required now that we don't reload for scanners.
    # @callback
    # def async_call_update_entry(self, confdata_scanners) -> None:
    #     """
    #     Call in the event loop to update the scanner entries in our config.

    #     We do this via add_job to ensure it runs in the event loop.
    #     """
    #     # Clear the flag for init and update the stamp
    #     self._do_full_scanner_init = False
    #     self.last_config_entry_update = monotonic_time_coarse()
    #     # Apply new config (will cause reload if there are changes)
    #     self.hass.config_entries.async_update_entry(
    #         self.config_entry,
    #         data={
    #             **self.config_entry.data,
    #             CONFDATA_SCANNERS: confdata_scanners,
    #         },
    #     )

    async def service_dump_devices(self, call: ServiceCall) -> ServiceResponse:  # pylint: disable=unused-argument;
        """Return a dump of beacon advertisements by receiver."""
        out: dict[str, Any] = {}
        addresses_input = call.data.get("addresses", "")
        redact = call.data.get("redact", False)
        configured_devices = call.data.get("configured_devices", False)
        summary: dict[str, Any] | None = None

        # Choose filter for device/address selection
        addresses: list[str] = []
        if addresses_input != "":
            # Specific devices
            addresses += addresses_input.upper().split()
        if configured_devices:
            # configured and scanners
            addresses += self.scanner_list
            configured_devices_option = self.options.get(CONF_DEVICES, [])
            if isinstance(configured_devices_option, list):
                addresses += [str(device) for device in configured_devices_option]
            # known IRK/Private BLE Devices
            addresses += list(self.pb_state_sources)

        dump_all_devices = addresses_input == "" and not configured_devices
        if dump_all_devices and len(self.devices) > DUMP_DEVICE_SOFT_LIMIT:
            fallback_addresses: set[str] = set(self.scanner_list)
            configured_devices_option = self.options.get(CONF_DEVICES, [])
            if isinstance(configured_devices_option, list):
                fallback_addresses.update(str(device) for device in configured_devices_option)
            fallback_addresses.update(
                str(source_address) for source_address in self.pb_state_sources.values() if source_address is not None
            )
            addresses = list(map(str.lower, fallback_addresses))
            summary = {
                "limited": True,
                "reason": (
                    f"Device dump limited to configured devices because total devices "
                    f"({len(self.devices)}) exceeded soft cap ({DUMP_DEVICE_SOFT_LIMIT})."
                ),
                "requested_devices": len(self.devices),
                "returned_devices": len(addresses),
            }

        # lowercase all the addresses for matching
        addresses = list(map(str.lower, addresses))

        # Build the dict of devices
        for address, device in self.devices.items():
            if len(addresses) == 0 or address.lower() in addresses:
                out[address] = device.to_dict()

        if summary is not None:
            out = {"summary": summary, "devices": out}

        if redact:
            _stamp_redact = monotonic_time_coarse()
            out_response = cast("ServiceResponse", self.redact_data(out))
            _stamp_redact_elapsed = monotonic_time_coarse() - _stamp_redact
            if _stamp_redact_elapsed > 3:  # It should be fast now.
                _LOGGER.warning("Dump devices redaction took %2f seconds", _stamp_redact_elapsed)
            else:
                _LOGGER.debug("Dump devices redaction took %2f seconds", _stamp_redact_elapsed)
            return out_response

        return cast("ServiceResponse", out)

    def redaction_list_update(self):
        """
        Freshen or create the list of match/replace pairs that we use to
        redact MAC addresses. This gives a set of helpful address replacements
        that still allows identifying device entries without disclosing MAC
        addresses.
        """
        _stamp = monotonic_time_coarse()

        # counter for incrementing replacement names (eg, SCANNER_n). The length
        # of the existing redaction list is a decent enough starting point.
        i = len(self.redactions)

        # SCANNERS
        for non_lower_address in self.scanner_list:
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                for altmac in mac_explode_formats(address):
                    self.redactions[altmac] = f"{address[:2]}::SCANNER_{i}::{address[-2:]}"
        _LOGGER.debug("Redact scanners: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))
        # CONFIGURED DEVICES
        for non_lower_address in self.options.get(CONF_DEVICES, []):
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                if address.count("_") == 2:
                    self.redactions[address] = f"{address[:4]}::CFG_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    self.redactions[address.split("_")[0]] = f"{address[:4]}::CFG_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:
                    for altmac in mac_explode_formats(address):
                        self.redactions[altmac] = f"{address[:2]}::CFG_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is, but not a mac.
                    self.redactions[address] = f"CFG_OTHER_{1}_{address}"
        _LOGGER.debug("Redact confdevs: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))
        # EVERYTHING ELSE
        for non_lower_address, device in self.devices.items():
            address = non_lower_address.lower()
            if address not in self.redactions:
                # Only add if they are not already there.
                i += 1
                if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                    self.redactions[address] = f"{address[:4]}::IRK_DEV_{i}"
                elif address.count("_") == 2:
                    self.redactions[address] = f"{address[:4]}::OTHER_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    self.redactions[address.split("_")[0]] = f"{address[:4]}::OTHER_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:  # a MAC
                    for altmac in mac_explode_formats(address):
                        self.redactions[altmac] = f"{address[:2]}::OTHER_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is.
                    self.redactions[address] = f"OTHER_{i}_{address}"
        _LOGGER.debug("Redact therest: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))
        _elapsed = monotonic_time_coarse() - _stamp
        if _elapsed > 0.5:
            _LOGGER.warning("Redaction list update took %.3f seconds, has %d items", _elapsed, len(self.redactions))
        else:
            _LOGGER.debug("Redaction list update took %.3f seconds, has %d items", _elapsed, len(self.redactions))
        self.stamp_redactions_expiry = monotonic_time_coarse() + PRUNE_TIME_REDACTIONS

    def redact_data(self, data: Any, first_recursion: bool = True) -> Any:  # noqa: FBT001
        """
        Wash any collection of data of any MAC addresses.

        Uses the redaction list of substitutions if already created, then
        washes any remaining mac-like addresses. This routine is recursive,
        so if you're changing it bear that in mind!
        """
        if first_recursion:
            # On first/outer call, refresh the redaction list to ensure
            # we don't let any new addresses slip through. Might be expensive
            # on first call, but will be much cheaper for subsequent calls.
            self.redaction_list_update()
            first_recursion = False

        if isinstance(data, str):  # Base Case
            datalower = data.lower()
            # the end of the recursive wormhole, do the actual work:
            if datalower in self.redactions:
                # Full string match, a quick short-circuit
                data = self.redactions[datalower]
            else:
                # Search for any of the redaction strings in the data.
                items = tuple(self.redactions.items())
                for find, fix in items:
                    if find in datalower:
                        data = datalower.replace(find, fix)
                        # don't break out because there might be multiple fixes required.
            # redactions done, now replace any remaining MAC addresses
            # We are only looking for xx:xx:xx... format.
            return self._redact_generic_re.sub(self._redact_generic_sub, data)
        elif isinstance(data, dict):
            return {self.redact_data(k, False): self.redact_data(v, False) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.redact_data(v, False) for v in data]
        else:  # Base Case
            return data
