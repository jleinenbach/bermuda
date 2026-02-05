"""Sensor platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import RestoreSensor, SensorEntity
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    MATCH_ALL,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    _LOGGER,
    ADDR_TYPE_FMDN_DEVICE,
    CONF_RECORDER_FRIENDLY,
    DEFAULT_RECORDER_FRIENDLY,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
)
from .entity import BermudaEntity, BermudaGlobalEntity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices: list[str] = []  # list of already-created devices
    created_scanners: dict[str, list[str]] = {}  # list of scanner:address for created entities

    @callback
    def device_new(address: str) -> None:
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        """
        # if len(scanners) == 0:
        #     # Bail out until we get called with some scanners to work with!
        #     return
        # for scanner in scanners:
        #     if (
        #         coordinator.devices[scanner]._is_remote_scanner is None  # usb/HCI scanner's are fine.
        #         or (
        #             coordinator.devices[scanner]._is_remote_scanner  # usb/HCI scanner's are fine.
        #             and coordinator.devices[scanner].address_wifi_mac is None
        #         )
        #     ):
        #         # This scanner doesn't have a wifi mac yet, bail out
        #         # until they are all filled out.
        #         return

        if address not in created_devices:
            # Check for duplicate entities with old address formats before creating new ones
            # This handles cases like FMDN address format changes (canonical_id vs device_id)
            old_address = coordinator.check_for_duplicate_entities(address)
            if old_address:
                # Clean up old entities to prevent duplicates
                coordinator.cleanup_old_entities_for_device(old_address, address)

            entities = []
            entities.append(BermudaSensor(coordinator, entry, address))
            if coordinator.have_floors:
                entities.append(BermudaSensorFloor(coordinator, entry, address))
            entities.append(BermudaSensorRange(coordinator, entry, address))
            entities.append(BermudaSensorScanner(coordinator, entry, address))
            entities.append(BermudaSensorRssi(coordinator, entry, address))
            entities.append(BermudaSensorAreaLastSeen(coordinator, entry, address))
            entities.append(BermudaSensorAreaSwitchReason(coordinator, entry, address))

            # Add FMDN-specific sensor (Estimated Broadcast Interval)
            # Since GoogleFindMy-HA doesn't receive Bluetooth itself (Bermuda handles that),
            # we provide the broadcast interval diagnostics for FMDN devices.
            if coordinator.devices[address].address_type == ADDR_TYPE_FMDN_DEVICE:
                entities.append(BermudaSensorEstimatedBroadcastInterval(coordinator, entry, address))

            # _LOGGER.debug("Sensor received new_device signal for %s", address)
            # We set update before add to False because we are being
            # call(back(ed)) from the update, so causing it to call another would be... bad.
            async_add_entities(entities, False)
            created_devices.append(address)
        else:
            # We've already created this one.
            # _LOGGER.debug("Ignoring duplicate creation request for %s", address)
            pass
        # Get the per-scanner entities set up to match
        create_scanner_entities()
        # tell the co-ord we've done it.
        coordinator.sensor_created(address)  # type: ignore[no-untyped-call]

    def create_scanner_entities() -> None:
        # These are per-proxy entities on each device, and scanners may come and
        # go over time. So we need to maintain our matrix of which ones we have already
        # spun-up so we don't duplicate any.

        for scanner in coordinator.get_scanners:
            if (
                scanner.is_remote_scanner is None  # usb/HCI scanner's are fine.
                or (scanner.is_remote_scanner and scanner.address_wifi_mac is None)
            ):
                # This scanner doesn't have a wifi mac yet, bail out
                # until they are all filled out.
                return

        entities = []
        for scanner_address in coordinator.scanner_list:
            for address in created_devices:
                if address not in created_scanners.get(scanner_address, []):
                    _LOGGER.debug(
                        "Creating Scanner %s entities for %s",
                        scanner_address,
                        address,
                    )
                    entities.append(BermudaSensorScannerRange(coordinator, entry, address, scanner_address))
                    entities.append(BermudaSensorScannerRangeRaw(coordinator, entry, address, scanner_address))
                    created_entry = created_scanners.setdefault(scanner_address, [])
                    created_entry.append(address)
        # _LOGGER.debug("Sensor received new_device signal for %s", address)
        # We set update before add to False because we are being
        # call(back(ed)) from the update, so causing it to call another would be... bad.
        async_add_entities(entities, False)

    @callback
    def scanners_changed() -> None:
        """Callback for event from coordinator advising that the roster of scanners has changed."""
        create_scanner_entities()

    # Connect device_new to a signal so the coordinator can call it
    _LOGGER.debug("Registering device_new and scanners_changed callbacks")
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_SCANNERS_CHANGED, scanners_changed))

    # Create Global Bermuda entities
    async_add_entities(
        (
            BermudaTotalProxyCount(coordinator, entry),
            BermudaActiveProxyCount(coordinator, entry),
            BermudaTotalDeviceCount(coordinator, entry),
            BermudaVisibleDeviceCount(coordinator, entry),
        )
    )


class BermudaSensor(BermudaEntity, SensorEntity):
    """bermuda Sensor class."""

    _attr_has_entity_name = True
    _attr_translation_key = "area"

    # Exclude time-based metadata from HA recorder database.
    # These 3 float attributes change every coordinator cycle (~1.05s),
    # forcing a new DB row per cycle per entity (Area, Floor, Distance).
    # They are pure real-time diagnostics with no historical value.
    # Live state, UI, automations and templates are NOT affected.
    _unrecorded_attributes = frozenset(
        {
            "last_good_area_age_s",
            "last_good_distance_age_s",
            "area_retention_seconds_remaining",
        }
    )

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return self._device.unique_id  # type: ignore[return-value]

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        # return self.coordinator.data.get("body")
        return self._device.area_name

    @property
    def icon(self) -> str | None:
        """Provide a custom icon for particular entities."""
        # Use translation_key to check entity type instead of name
        # (name would return translated string which varies by locale)
        if self._attr_translation_key == "area":
            return self._device.area_icon
        if self._attr_translation_key == "area_last_seen":
            return self._device.area_last_seen_icon
        if self._attr_translation_key == "floor":
            return self._device.floor_icon
        return super().icon

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Declare if entity should be automatically enabled on adding."""
        return self._attr_translation_key in ["area", "distance", "floor"]

    @property
    def device_class(self) -> str:  # type: ignore[override]
        """Return de device class of the sensor."""
        # There isn't one for "Area Names" so we'll arbitrarily define our own.
        return "bermuda__custom_device_class"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Provide state_attributes for the sensor entity."""
        # current_mac is pre-computed in BermudaDevice.calculate_data() for O(1) access.
        # For metadevices (iBeacon, Private BLE, FMDN), it tracks the most recent source MAC.
        # For regular devices, it's the device address itself.

        # Limit how many attributes we list - prefer new sensors instead
        # since oft-changing attribs cause more db writes than sensors
        # "last_seen": self.coordinator.dt_mono_to_datetime(self._device.last_seen),
        attribs: dict[str, Any] = {}
        # Use translation_key to check entity type instead of name
        if self._attr_translation_key in ["area", "floor"]:
            attribs["area_id"] = self._device.area_id
            attribs["area_name"] = self._device.area_name
            attribs["floor_id"] = self._device.floor_id
            attribs["floor_name"] = self._device.floor_name
            attribs["floor_level"] = self._device.floor_level
        if self._attr_translation_key in ["area", "floor", "distance"]:
            attribs.update(self._device.area_state_metadata())
        attribs["current_mac"] = self._device.current_mac

        return attribs


class BermudaSensorFloor(BermudaSensor):
    """Sensor for the Floor of the current Area."""

    _attr_translation_key = "floor"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_floor"

    @property
    def native_value(self) -> str | None:
        # Don't use area_scanner.name because it comes from the advert
        # entry. Instead refer to the BermudaDevice, which takes trouble
        # to use user-given names etc.
        return self._device.floor_name


class BermudaSensorScanner(BermudaSensor):
    """
    Sensor for name of nearest detected scanner.

    This sensor reports the name of the scanner that currently has the
    strongest signal to the tracked device (area_advert.scanner_address).

    Note: We use .get() for the scanner lookup because the scanner device
    may have been pruned from coordinator.devices in edge cases:
    - Scanner was demoted (is_scanner=False) and then pruned
    - Unusual configurations where scanners have rotating addresses
    In these cases, the advert still references the old scanner_address
    but the device entry no longer exists. Returning None is safe here.
    """

    _attr_translation_key = "nearest_scanner"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_scanner"

    @property
    def native_value(self) -> str | None:
        """
        Return the name of the nearest scanner.

        Uses .get() for defensive lookup - the scanner device may have been
        pruned from coordinator.devices while area_advert still references it.
        This can happen when a scanner is demoted and pruned, or in unusual
        configurations. Returns None if scanner not found.
        """
        if self._device.area_advert is not None:
            scanner_address = self._device.area_advert.scanner_address
            scanner_device = self.coordinator.devices.get(scanner_address)
            if scanner_device is not None:
                return scanner_device.name
        return None


class BermudaSensorRssi(BermudaSensor):
    """Sensor for RSSI of closest scanner."""

    _attr_translation_key = "nearest_rssi"

    @property
    def unique_id(self) -> str:
        """Return unique id for the entity."""
        return f"{self._device.unique_id}_rssi"

    @property
    def native_value(self) -> str | None:
        return self._cached_ratelimit(self._device.area_rssi, fast_falling=False, fast_rising=True)  # type: ignore[no-any-return]

    @property
    def device_class(self) -> str:  # type: ignore[override]
        return SensorDeviceClass.SIGNAL_STRENGTH

    @property
    def native_unit_of_measurement(self) -> str:
        return SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    @callback
    def _handle_coordinator_update(self) -> None:
        """Sync _attr_state_class with recorder-friendly option on every cycle."""
        if self.coordinator.options.get(CONF_RECORDER_FRIENDLY, DEFAULT_RECORDER_FRIENDLY):
            self._attr_state_class = None
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        super()._handle_coordinator_update()


class BermudaSensorRange(BermudaSensor):
    """Extra sensor for range-to-closest-area."""

    _attr_translation_key = "distance"

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"{self._device.unique_id}_range"

    @property
    def native_value(self) -> str | None:
        """Return the native value of the sensor."""
        distance = self._device.area_distance
        if distance is not None:
            return self._cached_ratelimit(round(distance, 1))  # type: ignore[no-any-return]
        return None

    @property
    def device_class(self) -> str:  # type: ignore[override]
        return SensorDeviceClass.DISTANCE

    @property
    def native_unit_of_measurement(self) -> str:
        """Results are in metres."""
        return UnitOfLength.METERS

    @callback
    def _handle_coordinator_update(self) -> None:
        """Sync _attr_state_class with recorder-friendly option on every cycle."""
        if self.coordinator.options.get(CONF_RECORDER_FRIENDLY, DEFAULT_RECORDER_FRIENDLY):
            self._attr_state_class = None
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        super()._handle_coordinator_update()


class BermudaSensorScannerRange(BermudaSensorRange):
    """Create sensors for range to each scanner. Extends closest-range class."""

    _attr_translation_key = "scanner_distance"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
        address: str,
        scanner_address: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._device = coordinator.devices[address]
        self._scanner = coordinator.devices[scanner_address]
        self._attr_translation_placeholders = {"scanner_name": self._scanner.name or ""}

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Sync _unrecorded_attributes with recorder-friendly option on every cycle.

        Extends parent's state_class sync with per-scanner attribute exclusion.
        MATCH_ALL excludes ALL extra_state_attributes from the recorder DB.

        The recorder reads unrecorded_attributes from _state_info (a mutable dict
        set once in async_internal_added_to_hass). We mutate it here so the
        recorder sees the updated value on the next state write.

        When recorder-friendly is OFF, we restore the class-level defaults
        computed by Entity.__init_subclass__ (entity component exclusions like
        "options" from SensorEntity + base class exclusions like the 3 time-based
        attrs from BermudaSensor). Using type(self) accesses the CLASS-level
        attributes, bypassing any instance-level overrides.
        """
        if self.coordinator.options.get(CONF_RECORDER_FRIENDLY, DEFAULT_RECORDER_FRIENDLY):
            new_unrecorded: frozenset[str] = frozenset({MATCH_ALL})
        else:
            # Restore class-level defaults (entity component + base class exclusions).
            # type(self).attr accesses the CLASS attribute via MRO, not the
            # instance attribute we may have set on a previous cycle.
            new_unrecorded = type(self)._entity_component_unrecorded_attributes | type(self)._unrecorded_attributes
        self._unrecorded_attributes = new_unrecorded
        # Propagate to _state_info so the recorder sees the change immediately.
        # _state_info is None before async_internal_added_to_hass runs.
        if self._state_info is not None:
            self._state_info["unrecorded_attributes"] = new_unrecorded
        super()._handle_coordinator_update()

    @property
    def unique_id(self) -> str:
        # Retaining legacy wifi mac for unique_id
        return f"{self._device.unique_id}_{self._scanner.address_wifi_mac or self._scanner.address}_range"

    @property
    def native_value(self) -> str | None:
        """
        Expose distance to given scanner.

        Don't break if that scanner's never heard of us!
        """
        distance = None
        if (scanner := self._device.get_scanner(self._scanner.address)) is not None:
            distance = scanner.rssi_distance
        if distance is not None:
            return self._cached_ratelimit(round(distance, 3))  # type: ignore[no-any-return]
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """We need to reimplement this, since the attributes need to be scanner-specific."""
        devscanner = self._device.get_scanner(self._scanner.address)
        if hasattr(devscanner, "source"):
            return {
                "area_id": self._scanner.area_id,
                "area_name": self._scanner.area_name,
                "area_scanner_mac": self._scanner.address,
                "area_scanner_name": self._scanner.name,
            }
        else:
            return None


class BermudaSensorScannerRangeRaw(BermudaSensorScannerRange):
    """Provides un-filtered latest distances per-scanner."""

    _attr_translation_key = "scanner_distance_raw"

    @property
    def unique_id(self) -> str:
        # Using address_wifi_mac as a legacy action, because esphome changed from
        # sending WIFI MAC to BLE MAC as its source address, in ESPHome 2025.3.0
        #
        return f"{self._device.unique_id}_{self._scanner.address_wifi_mac or self._scanner.address}_range_raw"

    @property
    def native_value(self) -> str | None:
        """
        Expose distance to given scanner.

        Don't break if that scanner's never heard of us!
        When recorder-friendly mode is enabled, apply rate limiting to reduce DB writes.
        """
        devscanner = self._device.get_scanner(self._scanner.address)
        distance = getattr(devscanner, "rssi_distance_raw", None)
        if distance is not None:
            rounded = round(distance, 3)
            if self.coordinator.options.get(CONF_RECORDER_FRIENDLY, DEFAULT_RECORDER_FRIENDLY):
                return self._cached_ratelimit(rounded)  # type: ignore[no-any-return]
            return rounded  # type: ignore[no-any-return]
        return None


class BermudaSensorAreaSwitchReason(BermudaSensor):
    """Sensor for area switch reason."""

    _attr_translation_key = "area_switch_diagnostic"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_area_switch_reason"

    @property
    def native_value(self) -> str | None:
        if self._device.diag_area_switch is not None:
            return self._device.diag_area_switch[:255]
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return structured diagnostic data as attributes."""
        if self._device.area_tests is None:
            return None
        return self._device.area_tests.to_dict()


class BermudaSensorEstimatedBroadcastInterval(BermudaSensor):
    """
    Estimated broadcast interval sensor for FMDN devices.

    This sensor shows the learned advertising interval for FMDN devices
    (Google Find My), calculated from Bermuda's own observation data.
    Since FMDN devices rotate their MAC addresses, HA's Bluetooth learned
    intervals don't work - we calculate from the actual observed intervals
    between advertisements.
    """

    _attr_translation_key = "estimated_broadcast_interval"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 1

    @property
    def unique_id(self) -> str:
        """Return unique id for the entity."""
        return f"{self._device.unique_id}_estimated_broadcast_interval"

    @property
    def native_value(self) -> float | None:
        """
        Return the estimated broadcast interval in seconds.

        Calculates the median interval from Bermuda's observed advertisement
        intervals across all scanners. Uses median to be robust against
        occasional missed packets or outliers.
        """
        # Collect all valid intervals from all adverts (scanner observations)
        all_intervals: list[float] = []
        for advert in self._device.adverts.values():
            # hist_interval contains time deltas between consecutive advertisements
            if hasattr(advert, "hist_interval") and advert.hist_interval:
                # Only include valid positive intervals (filter out None and negatives)
                all_intervals.extend(
                    interval for interval in advert.hist_interval if interval is not None and interval > 0
                )

        if not all_intervals:
            return None

        # Return median interval (robust against outliers from missed packets)
        all_intervals.sort()
        mid = len(all_intervals) // 2
        if len(all_intervals) % 2 == 0:
            return (all_intervals[mid - 1] + all_intervals[mid]) / 2
        return all_intervals[mid]


class BermudaSensorAreaLastSeen(BermudaSensor, RestoreSensor):
    """Sensor for name of last seen area."""

    _attr_translation_key = "area_last_seen"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_area_last_seen"

    @property
    def native_value(self) -> str | None:
        return self._device.area_last_seen

    async def async_added_to_hass(self) -> None:
        """Restore last saved value before adding to HASS."""
        await super().async_added_to_hass()
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = str(sensor_data.native_value)
            self._device.area_last_seen = str(sensor_data.native_value)


class BermudaGlobalSensor(BermudaGlobalEntity, SensorEntity):
    """bermuda Global Sensor class."""

    _attr_has_entity_name = True
    _attr_translation_key = "area"

    @property
    def device_class(self) -> str:  # type: ignore[override]
        """Return de device class of the sensor."""
        return "bermuda__custom_device_class"


class BermudaTotalProxyCount(BermudaGlobalSensor):
    """Counts the total number of proxies we have access to."""

    _attr_translation_key = "total_proxy_count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_PROXY_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the number of proxies we have access to."""
        return self._cached_ratelimit(len(self.coordinator.scanner_list)) or 0  # type: ignore[attr-defined]


class BermudaActiveProxyCount(BermudaGlobalSensor):
    """Counts the number of proxies that are active."""

    _attr_translation_key = "active_proxy_count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_ACTIVE_PROXY_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the number of proxies we have access to."""
        return self._cached_ratelimit(self.coordinator.count_active_scanners()) or 0  # type: ignore[attr-defined]


class BermudaTotalDeviceCount(BermudaGlobalSensor):
    """Counts the total number of devices we can see."""

    _attr_translation_key = "total_device_count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_DEVICE_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the amount of devices we have seen."""
        return self._cached_ratelimit(len(self.coordinator.devices)) or 0  # type: ignore[attr-defined]


class BermudaVisibleDeviceCount(BermudaGlobalSensor):
    """Counts the number of devices that are active."""

    _attr_translation_key = "visible_device_count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_VISIBLE_DEVICE_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the amount of devices that are active."""
        return self._cached_ratelimit(self.coordinator.count_active_devices()) or 0  # type: ignore[attr-defined]
