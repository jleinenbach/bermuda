"""DataUpdateCoordinator for Bermuda bluetooth data."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
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

from .area_selection import AreaSelectionHandler, AreaTests
from .bermuda_device import BermudaDevice
from .bermuda_irk import BermudaIrkManager
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
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
    CONF_USE_UKF_AREA_SELECTION,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USE_UKF_AREA_SELECTION,
    DOMAIN,
    DOMAIN_GOOGLEFINDMY,
    EVIDENCE_WINDOW_SECONDS,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_DEFAULT,
    PRUNE_TIME_FMDN,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_KNOWN_IRK,
    PRUNE_TIME_UNKNOWN_IRK,
    REPAIR_SCANNER_WITHOUT_AREA,
    SAVEOUT_COOLDOWN,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
    UPDATE_INTERVAL,
)
from .correlation import AreaProfile, CorrelationStore, RoomProfile
from .filters import UnscentedKalmanFilter  # noqa: TC001 - needed for runtime type hint
from .fmdn import FmdnIntegration
from .metadevice_manager import MetadeviceManager
from .scanner_calibration import ScannerCalibrationManager, update_scanner_calibration
from .services import BermudaServiceHandler
from .util import is_mac_address, normalize_address, normalize_mac

Cancellable = Callable[[], None]
CORRELATION_SAVE_INTERVAL = 300  # Save learned correlations every 5 minutes


if TYPE_CHECKING:
    from habluetooth import BaseHaScanner, BluetoothServiceInfoBleak
    from homeassistant.components.bluetooth import (
        BluetoothChange,
    )
    from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager

    from . import BermudaConfigEntry

# Using "if" instead of "min/max" triggers PLR1730, but when
# split over two lines, ruff removes it, then complains again.
# so we're just disabling it for the whole file.
# https://github.com/astral-sh/ruff/issues/4244


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

        # Service handler for dump_devices and redaction
        self.service_handler = BermudaServiceHandler(self)

        # Area selection handler for UKF and min-distance logic
        self.area_selection = AreaSelectionHandler(self)
        self.metadevice_manager = MetadeviceManager(self)

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

                # ============================================================
                # RESOLUTION FIRST: Identity resolvers run BEFORE any filtering
                # ============================================================
                # Extract service_data early so resolvers can inspect it before
                # any logic might discard or filter the device.
                # Note: SERVICE_UUID_FMDN detection happens inside handle_advertisement,
                # and RPA detection (first char in 4567) happens inside check_mac.
                service_data_raw = advertisementdata.service_data or {}
                service_data = cast("Mapping[str | int, Any]", service_data_raw)

                # Create/get the device - this always succeeds, never filters
                device = self._get_or_create_device(bledevice.address)
                device.process_advertisement(scanner_device, advertisementdata)

                # Google FMDN Resolution: Must run on EVERY advertisement.
                # This is critical for rotating MAC addresses - the resolver must have
                # a chance to "claim" the device and link it to a metadevice.
                # handle_advertisement checks for FMDN service data (UUID 0xFEAA)
                # internally and returns early if not present.
                self.fmdn.handle_advertisement(device, service_data)

                # Apple IRK Resolution: Check if this MAC matches any known IRKs.
                # This is called on every advertisement to catch cases where:
                # 1. The IRK was learned after the device was first seen
                # 2. The MAC rotated to a new address that now matches a known IRK
                # The check is cheap because irk_manager caches results.
                self.irk_manager.check_mac(bledevice.address)

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

        # Prune redaction data (stored in service_handler)
        sh = self.service_handler
        if sh.stamp_redactions_expiry is not None and sh.stamp_redactions_expiry < nowstamp:
            _LOGGER.debug("Clearing redaction data (%d items)", len(sh.redactions))
            sh.redactions.clear()
            sh.stamp_redactions_expiry = None

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
                        # FIX: Prevent duplicates - device may appear in multiple metadevices
                        elif address not in prune_list:
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

        # FIX: Safety deduplication - devices may appear in multiple metadevices' sources
        # which could cause the same address to be added multiple times
        prune_list = list(dict.fromkeys(prune_list))  # Preserves order, removes duplicates

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

            # Clean out adverts that reference pruned devices.
            # Adverts store two device addresses:
            #   - device_address: The tracked device (tracker/beacon) that emitted the BLE advertisement
            #   - scanner_address: The scanner (ESPHome proxy, BT adapter) that received it
            # We must prune adverts if EITHER address is in the prune_list, otherwise
            # code accessing devices[address] will raise KeyError.
            for advert_tuple in list(device.adverts.keys()):
                advert = device.adverts[advert_tuple]
                # Case 1: The tracked device (tracker) is being pruned
                # This happens when a tracker with rotating RPA goes stale
                if advert.device_address in prune_list:
                    _LOGGER.debug(
                        "Pruning advert for pruned tracker %s (device: %s, scanner: %s, age: %ds)",
                        advert_tuple,
                        advert.device_address,
                        advert.scanner_address,
                        nowstamp - advert.stamp,
                    )
                    del device.adverts[advert_tuple]
                # Case 2: The scanner that received the advert is being pruned
                # This is rare but can happen if a scanner is demoted and pruned
                elif advert.scanner_address in prune_list:
                    _LOGGER.debug(
                        "Pruning advert for pruned scanner %s (device: %s, scanner: %s, age: %ds)",
                        advert_tuple,
                        advert.device_address,
                        advert.scanner_address,
                        nowstamp - advert.stamp,
                    )
                    del device.adverts[advert_tuple]
                    # Clear area_advert if it points to the pruned scanner,
                    # otherwise BermudaSensorScanner.native_value would fail
                    if device.area_advert is advert:
                        _LOGGER.debug(
                            "Clearing area_advert for %s (referenced pruned scanner %s)",
                            device.name,
                            advert.scanner_address,
                        )
                        device.area_advert = None

    def discover_private_ble_metadevices(self) -> None:
        """Delegate to metadevice_manager for Private BLE device discovery."""
        self.metadevice_manager.discover_private_ble_metadevices()

    def register_ibeacon_source(self, source_device: BermudaDevice) -> None:
        """Delegate to metadevice_manager for iBeacon source registration."""
        self.metadevice_manager.register_ibeacon_source(source_device)

    def update_metadevices(self) -> None:
        """Delegate to metadevice_manager for metadevice updates."""
        self.metadevice_manager.update_metadevices()

    def dt_mono_to_datetime(self, stamp: float) -> datetime:
        """Given a monotonic timestamp, convert to datetime object."""
        age = float(monotonic_time_coarse() - stamp)
        return now() - timedelta(seconds=age)

    def dt_mono_to_age(self, stamp: float) -> str:
        """Convert monotonic timestamp to age (eg: "6 seconds ago")."""
        return get_age(self.dt_mono_to_datetime(stamp))

    # AreaTests dataclass is imported from area_selection module.
    # Kept as class attribute for backward compatibility with tests.
    AreaTests = AreaTests

    def _refresh_areas_by_min_distance(self) -> None:
        """
        Set area for ALL devices based on UKF+RoomProfile or min-distance fallback.

        Delegates to AreaSelectionHandler for the main loop and device processing.
        """
        self.area_selection.refresh_areas_by_min_distance()

    def _refresh_area_by_min_distance(self, device: BermudaDevice) -> None:
        """Delegate to area_selection handler for min-distance area detection."""
        self.area_selection._refresh_area_by_min_distance(device)  # noqa: SLF001

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

    async def service_dump_devices(self, call: ServiceCall) -> ServiceResponse:
        """
        Return a dump of beacon advertisements by receiver.

        Delegates to the service handler for actual implementation.
        """
        return await self.service_handler.async_dump_devices(call)
