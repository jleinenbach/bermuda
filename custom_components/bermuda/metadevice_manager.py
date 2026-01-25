"""
Metadevice management for Bermuda BLE Trilateration.

This module handles the creation and updating of meta-devices, which are
logical devices that aggregate data from multiple physical BLE addresses.

Meta-device types:
- Private BLE Device (IRK): Devices using Identity Resolving Keys
- iBeacon: Apple iBeacon protocol devices
- FMDN: Google Find My Device Network devices

The MetadeviceManager follows Home Assistant's handler pattern (similar to
ESPHome, ZHA, Bluetooth integrations) where complex functionality is
extracted into dedicated handler classes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.const import Platform

from .const import (
    CONF_DEVICES,
    DOMAIN_PRIVATE_BLE_DEVICE,
    EVIDENCE_WINDOW_SECONDS,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
)
from .util import normalize_address, normalize_mac

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceRegistry
    from homeassistant.helpers.entity_registry import EntityRegistry

    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator
    from .fmdn import FmdnIntegration

_LOGGER = logging.getLogger(__name__)


class MetadeviceManager:
    """
    Handler for metadevice discovery and updates.

    This class manages the lifecycle of meta-devices:
    - Discovery of Private BLE Device integration entities
    - Registration of iBeacon sources
    - Periodic updates to aggregate source device data

    Attributes
    ----------
        coordinator: Reference to the parent coordinator for state access.

    """

    def __init__(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Initialize the metadevice manager.

        Args:
        ----
            coordinator: The parent BermudaDataUpdateCoordinator instance.

        """
        self.coordinator = coordinator

    # =========================================================================
    # Property accessors for coordinator state
    # =========================================================================

    @property
    def hass(self) -> HomeAssistant:
        """Access Home Assistant instance."""
        return self.coordinator.hass

    @property
    def er(self) -> EntityRegistry:
        """Access entity registry."""
        return self.coordinator.er

    @property
    def dr(self) -> DeviceRegistry:
        """Access device registry."""
        return self.coordinator.dr

    @property
    def options(self) -> dict[str, Any]:
        """Access coordinator options."""
        return self.coordinator.options

    @property
    def metadevices(self) -> dict[str, BermudaDevice]:
        """Access metadevices dictionary."""
        return self.coordinator.metadevices

    @property
    def pb_state_sources(self) -> dict[str, str | None]:
        """Access Private BLE state sources tracking."""
        return self.coordinator.pb_state_sources

    @property
    def fmdn(self) -> FmdnIntegration:
        """Access FMDN integration."""
        return self.coordinator.fmdn

    @property
    def _do_private_device_init(self) -> bool:
        """Check if Private BLE device init is needed."""
        return self.coordinator._do_private_device_init  # noqa: SLF001

    @_do_private_device_init.setter
    def _do_private_device_init(self, value: bool) -> None:
        """Set Private BLE device init flag."""
        self.coordinator._do_private_device_init = value  # noqa: SLF001

    # =========================================================================
    # Helper methods (delegate to coordinator)
    # =========================================================================

    def _get_or_create_device(self, address: str) -> BermudaDevice:
        """Get or create a BermudaDevice by address."""
        return self.coordinator._get_or_create_device(address)  # noqa: SLF001

    def _get_device(self, address: str) -> BermudaDevice | None:
        """Get a BermudaDevice by address (returns None if not found)."""
        return self.coordinator._get_device(address)  # noqa: SLF001

    # =========================================================================
    # Main metadevice methods
    # =========================================================================

    def discover_private_ble_metadevices(self) -> None:
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

                        # Validate IRK format - must be exactly 32 hex characters (16 bytes)
                        if len(_irk) != 32 or not all(c in "0123456789abcdefABCDEF" for c in _irk):
                            _LOGGER.error(
                                "Invalid IRK extracted from Private BLE Device %s: "
                                "expected 32 hex characters, got '%s' (length: %d) from unique_id '%s'. "
                                "This may indicate a change in Private BLE Device integration format.",
                                pb_entity.entity_id,
                                _irk,
                                len(_irk),
                                pb_entity.unique_id,
                            )
                            continue  # Skip this invalid device

                        _LOGGER.debug(
                            "Extracted valid IRK from Private BLE Device %s: %s (from unique_id: %s)",
                            pb_entity.entity_id,
                            _irk,
                            pb_entity.unique_id,
                        )

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

                        # Register metadevice in BOTH metadevices AND devices dictionaries.
                        # The config_flow.py iterates over coordinator.devices (line 293),
                        # so metadevices MUST be in coordinator.devices to appear in the UI.
                        if metadevice.address not in self.metadevices:
                            self.metadevices[metadevice.address] = metadevice
                        # Ensure metadevice is also in coordinator.devices for config flow visibility
                        if metadevice.address not in self.coordinator.devices:
                            self.coordinator.devices[metadevice.address] = metadevice

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

        Args:
        ----
            source_device: The BermudaDevice that is advertising the iBeacon.

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
                # Register metadevice in BOTH metadevices AND devices dictionaries.
                # The config_flow.py iterates over coordinator.devices (line 293),
                # so metadevices MUST be in coordinator.devices to appear in the UI.
                if metadevice.address not in self.metadevices:
                    self.metadevices[metadevice.address] = metadevice
                # Ensure metadevice is also in coordinator.devices for config flow visibility
                if metadevice.address not in self.coordinator.devices:
                    self.coordinator.devices[metadevice.address] = metadevice

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

    def update_metadevices(self) -> None:
        """
        Create or update iBeacon, Private_BLE and other meta-devices.

        This aggregates data from received advertisements into meta-devices.
        Must be run on each update cycle, after the calculations for each source
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
            _sources_to_remove: list[str] = []

            for source_address in metadevice.metadevice_sources:
                # Get the BermudaDevice holding those adverts
                # TODO: Verify it's OK to not create here. Problem is that if we do create,
                # it causes a binge/purge cycle during pruning since it has no adverts on it.
                source_device = self._get_device(source_address)
                if source_device is None:
                    # No ads current in the backend for this one. Not an issue, the mac might be old
                    # or now showing up yet.
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
                        "Source %s for metadev %s changed iBeacon identifiers, severing",
                        source_device,
                        metadevice,
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
                metadevice.last_seen = max(metadevice.last_seen, source_device.last_seen)

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
                for key, val in vars(source_device).items():
                    if val is any(
                        [
                            source_device.name_bt_local_name,
                            source_device.name_bt_serviceinfo,
                            source_device.manufacturer,
                        ]
                    ) and getattr(metadevice, key, None) in [None, False]:
                        setattr(metadevice, key, val)
                        _want_name_update = True

                if _want_name_update:
                    metadevice.make_name()

                # Anything that's VERY interesting, overwrite it regardless of what's already there:
                # INTERESTING:
                for key, val in vars(source_device).items():
                    if val is any(
                        [
                            source_device.beacon_major,
                            source_device.beacon_minor,
                            source_device.beacon_power,
                            source_device.beacon_unique_id,
                            source_device.beacon_uuid,
                        ]
                    ):
                        setattr(metadevice, key, val)
                        # _want_name_update = True

            # Done iterating sources, remove any to be dropped
            for source in _sources_to_remove:
                metadevice.metadevice_sources.remove(source)
            if _want_name_update:
                metadevice.make_name()

    def aggregate_source_data_to_metadevices(self) -> None:
        """
        Aggregate area/distance data from source devices into metadevices.

        This method runs AFTER area selection has completed, so source devices
        have their area_id, area_name, area_distance, etc. populated.

        For each metadevice, we find the "best" active source (most recent
        last_seen within evidence window, with valid area data) and copy
        its area-related properties to the metadevice.

        This ensures metadevices have proper area data for sensors/trackers.
        """
        nowstamp = monotonic_time_coarse()
        evidence_cutoff = nowstamp - EVIDENCE_WINDOW_SECONDS

        for metadevice in self.metadevices.values():
            best_source: BermudaDevice | None = None
            best_last_seen: float = 0.0

            # Find the best active source device
            for source_address in metadevice.metadevice_sources:
                source_device = self._get_device(source_address)
                if source_device is None:
                    continue

                # Source must have been seen recently
                if source_device.last_seen < evidence_cutoff:
                    continue

                # Source must have valid area data
                if source_device.area_id is None:
                    continue

                # Prefer the source with most recent last_seen
                if source_device.last_seen > best_last_seen:
                    best_last_seen = source_device.last_seen
                    best_source = source_device

            if best_source is None:
                # No active source with area data found
                # Keep existing metadevice state (may go stale naturally)
                continue

            # Copy area-related data from best source to metadevice
            metadevice.area_id = best_source.area_id
            metadevice.area_name = best_source.area_name
            metadevice.area = best_source.area
            metadevice.area_icon = best_source.area_icon
            metadevice.area_distance = best_source.area_distance
            metadevice.area_distance_stamp = best_source.area_distance_stamp
            metadevice.area_rssi = best_source.area_rssi
            metadevice.area_advert = best_source.area_advert
            metadevice.area_last_seen = best_source.area_last_seen
            metadevice.area_last_seen_id = best_source.area_last_seen_id
            metadevice.area_last_seen_icon = best_source.area_last_seen_icon
            metadevice.area_state_stamp = best_source.area_state_stamp
            metadevice.area_state_source = best_source.area_state_source

            # Copy floor data
            metadevice.floor_id = best_source.floor_id
            metadevice.floor = best_source.floor
            metadevice.floor_name = best_source.floor_name
            metadevice.floor_level = best_source.floor_level

            # Update last_seen to match best source
            metadevice.last_seen = best_source.last_seen

            _LOGGER.debug(
                "Aggregated area data for metadevice %s from source %s: area=%s, distance=%.2fm",
                metadevice.name,
                best_source.address,
                metadevice.area_name,
                metadevice.area_distance if metadevice.area_distance is not None else -1,
            )
