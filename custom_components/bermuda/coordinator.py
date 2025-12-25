"""DataUpdateCoordinator for Bermuda bluetooth data."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

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
    AREA_MAX_AD_AGE,
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
    CROSS_FLOOR_MIN_HISTORY,
    CROSS_FLOOR_STREAK,
    DATA_EID_RESOLVER,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_FMDN_EID_FORMAT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    DOMAIN_GOOGLEFINDMY,
    DOMAIN_PRIVATE_BLE_DEVICE,
    EVIDENCE_WINDOW_SECONDS,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_DEFAULT,
    PRUNE_TIME_FMDN,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_KNOWN_IRK,
    PRUNE_TIME_REDACTIONS,
    PRUNE_TIME_UNKNOWN_IRK,
    REPAIR_SCANNER_WITHOUT_AREA,
    SAME_FLOOR_STREAK,
    SAVEOUT_COOLDOWN,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
    UPDATE_INTERVAL,
)
from .fmdn import extract_fmdn_eids
from .util import is_mac_address, mac_explode_formats, normalize_address, normalize_identifier, normalize_mac

Cancellable = Callable[[], None]
DUMP_DEVICE_SOFT_LIMIT = 1200

# Protocol definition kept small to avoid cross-integration dependency imports.
class EidResolver(Protocol):
    """Resolver interface exposed by the googlefindmy integration."""

    def resolve_eid(self, eid: bytes) -> Any:
        """Resolve an FMDN EID to device metadata."""


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
        self.hass_version_min_2025_2 = HA_VERSION_MAJ > 2025 or (HA_VERSION_MAJ == 2025 and HA_VERSION_MIN >= 2)
        # when habasescanner.discovered_device_timestamps became a public method.
        self.hass_version_min_2025_4 = HA_VERSION_MAJ > 2025 or (HA_VERSION_MAJ == 2025 and HA_VERSION_MIN >= 4)

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
                for conn_type, _conn_id in device_entry.connections:
                    if conn_type == "private_ble_device":
                        _LOGGER.debug("Trigger updating of Private BLE Devices")
                        self._do_private_device_init = True
                    elif conn_type == "ibeacon":
                        # this was probably us, nothing else to do
                        pass
                    else:
                        for ident_type, ident_id in device_entry.identifiers:
                            if ident_type == DOMAIN:
                                # One of our sensor devices!
                                try:
                                    if _device := self.devices[ident_id.lower()]:
                                        _device.name_by_user = device_entry.name_by_user
                                        _device.make_name()
                                except KeyError:
                                    pass
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
                _LOGGER.debug("Opportunistic trigger of update for Private BLE Devices")
                self._do_private_device_init = True
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

    def _get_fmdn_resolver(self) -> EidResolver | None:
        """Return the googlefindmy resolver from ``hass.data`` when present."""
        bucket = self.hass.data.get(DOMAIN_GOOGLEFINDMY)
        if not isinstance(bucket, dict):
            return None

        resolver = bucket.get(DATA_EID_RESOLVER)
        resolve_eid = getattr(resolver, "resolve_eid", None)
        if resolver is None:
            return None
        if not callable(resolve_eid):
            _LOGGER.debug("Resolver missing resolve_eid callable: %s", type(resolver))
            return None

        return cast("EidResolver", resolver)

    def _format_fmdn_metadevice_address(self, device_id: str, canonical_id: str | None) -> str:
        """Return the canonical key for an FMDN metadevice."""
        base = canonical_id or device_id
        return normalize_identifier(f"fmdn:{base}")

    @staticmethod
    def _normalize_eid_bytes(eid_data: bytes | bytearray | memoryview | str | None) -> bytes | None:
        """Return EID payload as bytes, accepting raw bytes or hex strings."""
        if eid_data is None:
            return None

        if isinstance(eid_data, (bytes, bytearray, memoryview)):
            return bytes(eid_data)

        if isinstance(eid_data, str):
            cleaned = eid_data.replace("0x", "").replace(":", "").replace(" ", "")
            try:
                return bytes.fromhex(cleaned)
            except ValueError:
                _LOGGER.debug("Failed to parse EID hex string: %s", eid_data)
                return None

        _LOGGER.debug("Unsupported EID payload type: %s", type(eid_data))
        return None

    def _extract_fmdn_eids(self, service_data: Mapping[str | int, Any]) -> set[bytes]:
        """Extract an FMDN EID using the configured format."""
        return extract_fmdn_eids(service_data, mode=DEFAULT_FMDN_EID_FORMAT)

    def _process_fmdn_resolution(self, eid_bytes: bytes) -> Any | None:
        """Resolve an EID payload to a Home Assistant device registry id."""
        resolver = self._get_fmdn_resolver()

        if resolver is None:
            return None

        normalized_eid = self._normalize_eid_bytes(eid_bytes)
        if normalized_eid is None:
            return None

        try:
            return resolver.resolve_eid(normalized_eid)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Resolver raised while processing EID payload", exc_info=True)
        return None

    def _register_fmdn_source(self, source_device: BermudaDevice, metadevice_address: str, match: Any) -> None:
        """Attach a rotating FMDN source MAC to its stable metadevice container."""
        metadevice = self._get_or_create_device(metadevice_address)
        metadevice.metadevice_type.add(METADEVICE_FMDN_DEVICE)
        metadevice.address_type = BDADDR_TYPE_NOT_MAC48
        metadevice.fmdn_device_id = getattr(match, "device_id", None)
        metadevice.fmdn_canonical_id = getattr(match, "canonical_id", None)

        if metadevice.address not in self.metadevices:
            self.metadevices[metadevice.address] = metadevice

        if metadevice.fmdn_device_id and (device_entry := self.dr.async_get(metadevice.fmdn_device_id)):
            metadevice.name_devreg = device_entry.name
            metadevice.name_by_user = device_entry.name_by_user
            metadevice.make_name()

        source_device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)

        if source_device.address not in metadevice.metadevice_sources:
            metadevice.metadevice_sources.insert(0, source_device.address)

    def _handle_fmdn_advertisement(self, device: BermudaDevice, service_data: Mapping[str | int, Any]) -> None:
        """Process FMDN payloads for an advertisement."""
        if not service_data:
            return

        candidates = self._extract_fmdn_eids(service_data)
        if not candidates:
            return

        device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)

        for eid_bytes in candidates:
            match = self._process_fmdn_resolution(eid_bytes)
            if match is None:
                continue

            resolved_device_id = getattr(match, "device_id", None)
            canonical_id = getattr(match, "canonical_id", None)
            is_shared = bool(getattr(match, "shared", False))

            if is_shared and resolved_device_id is None and canonical_id is None:
                _LOGGER.debug("Skipping shared FMDN match without identifiers")
                continue
            if resolved_device_id is None:
                _LOGGER.debug("Resolver returned match without device_id for candidate length %d", len(eid_bytes))
                continue

            metadevice_address = self._format_fmdn_metadevice_address(str(resolved_device_id), canonical_id)
            self._register_fmdn_source(device, metadevice_address, match)
            break

    def _maybe_prune_fmdn_source(
        self, device: BermudaDevice, stamp_fmdn: float, prune_list: list[str]
    ) -> bool:
        """Prune stale FMDN rotating MACs and return True if pruned."""
        if METADEVICE_TYPE_FMDN_SOURCE not in device.metadevice_type:
            return False
        if device.last_seen >= stamp_fmdn:
            return False

        prune_list.append(device.address)
        return True

    async def _async_update_data(self):
        """Implementation of DataUpdateCoordinator update_data function."""
        # return False
        return self._async_update_data_internal()

    def _async_update_data_internal(self):
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

    def _async_gather_advert_data(self):
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
                self._handle_fmdn_advertisement(device, service_data)

        # end of for ha_scanner loop
        return True

    def prune_devices(self, force_pruning=False):  # noqa: C901
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
                        if self._maybe_prune_fmdn_source(_device, stamp_fmdn, prune_list):
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

        # iBeacon devices should already have their metadevices created, so nothing more to
        # set up for them.

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

                # FIXME: This has two potential bugs:
                # - if multiple metadevices share a source, they will
                #   "fight" over their preferred ref_power, if different.
                # - The non-meta device (if tracked) will receive distances
                #   based on the meta device's ref_power.
                # - The non-meta device if tracked will have its own ref_power ignored.
                #
                # None of these are terribly awful, but worth fixing.

                # Note we are setting the ref_power on the source_device, not the
                # individual scanner entries (it will propagate to them though)
                if source_device.ref_power != metadevice.ref_power:
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

        if advert.stamp < nowstamp - AREA_MAX_AD_AGE:
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

    def _refresh_areas_by_min_distance(self):
        """Set area for ALL devices based on closest beacon."""
        for device in self.devices.values():
            if (
                # device.is_scanner is not True  # exclude scanners.
                device.create_sensor  # include any devices we are tracking
                # or device.metadevice_type in METADEVICE_SOURCETYPES  # and any source devices for PBLE, ibeacon etc
            ):
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
            inc_floor_id = getattr(incumbent_scanner, "floor_id", None) if incumbent_scanner else None
            inc_floor_level = (
                getattr(incumbent_scanner, "floor_level", None) if incumbent_scanner else None
            )
            chal_floor_id = getattr(challenger_scanner, "floor_id", None)
            chal_floor_level = getattr(challenger_scanner, "floor_level", None)
            tests.floors = (inc_floor_id, chal_floor_id)
            tests.floor_levels = (inc_floor_level, chal_floor_level)
            cross_floor = (
                inc_floor_id is not None and chal_floor_id is not None and inc_floor_id != chal_floor_id
            )

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

            if incumbent_scanner is None:
                tests.reason = "LOSS - incumbent missing scanner metadata"
                continue

            if current_incumbent.area_id is None:
                incumbent = challenger
                soft_incumbent = None
                continue

            if incumbent_distance is None:
                # No incumbent distance available; allow the challenger to compete to avoid deadlocks.
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

            if incumbent_distance < challenger_distance:
                # we are not even closer!
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
                if len(challenger_hist_all) < cross_floor_min_history or len(
                    incumbent_hist_all
                ) < cross_floor_min_history:
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
                    if (
                        tests.hist_min_max[1] < tests.hist_min_max[0]
                        and tests.pcnt_diff > pdiff_historical  # and we're significantly closer.
                    ):
                        tests.reason = "WIN on historical min/max"
                        incumbent = challenger
                        continue

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
                if not (sustained_cross_floor or tests.pcnt_diff >= cross_floor_escape):
                    tests.reason = "LOSS - cross-floor evidence insufficient"
                    continue

            if tests.pcnt_diff < pdiff_outright:
                # Allow a near-field absolute improvement to win even when percent diff is small.
                near_field_cutoff = 1.0
                abs_win_meters = 0.08
                if not (avg_dist <= near_field_cutoff and abs_diff >= abs_win_meters):
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
        winner = incumbent or soft_incumbent

        if not has_distance_contender:
            fallback_candidates: list[BermudaAdvert] = []
            for adv in device.adverts.values():
                if not _area_candidate(adv) or not _within_evidence(adv):
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
                incumbent_candidate = device.area_advert if _area_candidate(device.area_advert) else None
                best_rssi = best_by_rssi.rssi
                incumbent_rssi = incumbent_candidate.rssi if incumbent_candidate is not None else None
                if incumbent_candidate is None or best_by_rssi is incumbent_candidate:
                    winner = best_by_rssi
                    tests.reason = "WIN via RSSI fallback (no distance contenders)"
                elif best_rssi is not None and (
                    incumbent_rssi is None or best_rssi >= incumbent_rssi + rssi_fallback_margin
                ):
                    winner = best_by_rssi
                    tests.reason = "WIN via RSSI fallback margin"
                else:
                    winner = incumbent_candidate
                    tests.reason = "HOLD via RSSI fallback hysteresis"
            else:
                winner = None

        if device.area_advert != winner and tests.reason is not None:
            device.diag_area_switch = tests.sensortext()

        # Apply the newly-found closest scanner (or apply None if we didn't find one)
        def _resolve_cross_floor(current: BermudaAdvert | None, candidate: BermudaAdvert | None) -> bool:
            cur_floor = getattr(current.scanner_device, "floor_id", None) if current else None
            cand_floor = getattr(candidate.scanner_device, "floor_id", None) if candidate else None
            return cur_floor is not None and cand_floor is not None and cur_floor != cand_floor

        def _apply_selection(advert: BermudaAdvert | None) -> None:
            device.apply_scanner_selection(advert, nowstamp=nowstamp)

        if winner is None:
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
                if last_log_age > AREA_MAX_AD_AGE:
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

        if device.area_advert is None and winner is not None:
            # Bootstrap immediately when we have no area yet; don't wait for streak logic.
            device.pending_area_id = None
            device.pending_floor_id = None
            device.pending_streak = 0
            _apply_selection(winner)
            return

        if (
            device.pending_area_id == winner.area_id
            and device.pending_floor_id == getattr(winner.scanner_device, "floor_id", None)
        ):
            device.pending_streak += 1
        else:
            device.pending_area_id = winner.area_id
            device.pending_floor_id = getattr(winner.scanner_device, "floor_id", None)
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
                str(source_address)
                for source_address in self.pb_state_sources.values()
                if source_address is not None
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

    def redact_data(self, data, first_recursion=True):
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
