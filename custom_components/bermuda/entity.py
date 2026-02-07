"""BermudaEntity class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ADDR_TYPE_FMDN_DEVICE,
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    ATTRIBUTION,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    DOMAIN_GOOGLEFINDMY,
    DOMAIN_PRIVATE_BLE_DEVICE,
)
from .util import is_mac_address, mac_math_offset, normalize_mac

if TYPE_CHECKING:
    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator
    # from . import BermudaDevice


class BermudaEntity(CoordinatorEntity):
    """
    Co-ordinator for Bermuda data.

    Gathers the device infor for receivers and transmitters, calculates
    distances etc.
    """

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self.coordinator: BermudaDataUpdateCoordinator = coordinator
        self.config_entry = config_entry
        self.address = address
        self._device = coordinator.devices[address]
        self._lastname = self._device.name  # So we can track when we get a new name
        self.ar = ar.async_get(coordinator.hass)
        self.dr = dr.async_get(coordinator.hass)
        self.devreg_init_done = False

        self.bermuda_update_interval = config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        self.bermuda_last_state: Any = 0
        self.bermuda_last_stamp: float = 0

    def _cached_ratelimit(
        self,
        statevalue: Any,
        fast_falling: bool = True,  # noqa: FBT001
        fast_rising: bool = False,  # noqa: FBT001
        interval: float | None = None,
    ) -> Any:
        """
        Uses the CONF_UPDATE_INTERVAL and other logic to return either the given statevalue
        or an older, cached value. Helps to reduce excess sensor churn without compromising latency.

        Mostly suitable for MEASUREMENTS, but should work with strings, too.
        If interval is specified the cache will use that (in seconds), otherwise the deafult is
        the CONF_UPPDATE_INTERVAL (typically suitable for fast-close slow-far sensors)
        """
        if interval is not None:
            self.bermuda_update_interval = interval

        nowstamp = monotonic_time_coarse()
        if (
            (self.bermuda_last_stamp < nowstamp - self.bermuda_update_interval)  # Cache is stale
            or (self._device.ref_power_changed > nowstamp - 2)  # ref power changed in last 2sec
            or (self.bermuda_last_state is None)  # Nothing compares to you.
            or (statevalue is None)  # or you.
            or (fast_falling and statevalue < self.bermuda_last_state)  # (like Distance)
            or (fast_rising and statevalue > self.bermuda_last_state)  # (like RSSI)
        ):
            # Publish the new value and update cache
            self.bermuda_last_stamp = nowstamp
            self.bermuda_last_state = statevalue
            return statevalue
        else:
            # Send the cached value, don't update cache
            return self.bermuda_last_state

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the co-ordinator.

        Any specific things we want to do during an update cycle
        """
        if not self.devreg_init_done and self.device_entry:
            self._device.name_by_user = self.device_entry.name_by_user
            self.devreg_init_done = True
        if self._device.name != self._lastname:
            self._lastname = self._device.name
            if self.device_entry:
                # We have a new name locally, so let's update the device registry.
                self.dr.async_update_device(self.device_entry.id, name=self._device.name)
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID to use for this entity."""
        return self._device.unique_id

    @property
    def device_info(self) -> DeviceInfo | None:
        """
        Implementing this creates an entry in the device registry.

        This is responsible for linking Bermuda entities to devices,
        and also for matching up to device entries for other integrations.
        """
        # Match up our entity with any existing device entries.
        # Canonicalization note:
        # Bermuda uses util.normalize_mac() for MAC connections, which yields
        # lower-case, colon-delimited MACs. Device registry connections must
        # remain consistent to avoid duplicates that render identically in the UI.
        domain_name = DOMAIN
        model = None

        if self._device.is_scanner:
            # Scanner device congealment: use the scanner's native integration
            # device entry identifiers so Bermuda entities appear in the same
            # HA device as ESPHome/Shelly/Bluetooth entities.
            # This follows the same pattern as FMDN device congealment.
            #
            # Priority: Prefer ESPHome/Shelly device (via WiFi MAC or MAC
            # offset) over HA Bluetooth auto-created device. The ESPHome
            # device has proper firmware info, manufacturer, model etc.
            congealment_device = None

            # Priority 1: ESPHome/Shelly via WiFi MAC connection
            if self._device.address_wifi_mac:
                candidate = self.dr.async_get_device(
                    connections={(dr.CONNECTION_NETWORK_MAC, normalize_mac(self._device.address_wifi_mac))}
                )
                # Verify it's NOT a BT device with a polluted "mac" connection
                if candidate is not None and not any(c[0] == "bluetooth" for c in candidate.connections):
                    congealment_device = candidate

            # Priority 2: MAC-offset search for ESPHome/Shelly device.
            # Handles cases where scanner resolution failed (ESPHome not yet
            # loaded, non-standard MAC offset, etc.). Searches device registry
            # for any device with CONNECTION_NETWORK_MAC matching a MAC offset
            # (-3 to +2) from the scanner's BLE address.
            # Skip candidates that have "bluetooth" connections — those are
            # BT devices with polluted "mac" entries, not ESPHome/Shelly.
            if congealment_device is None or not congealment_device.identifiers:
                base_addr = self._device.address_ble_mac or self._device.address
                for offset in range(-3, 3):
                    alt_mac = mac_math_offset(base_addr, offset)
                    if alt_mac is None:
                        continue
                    try:
                        alt_mac_norm = normalize_mac(alt_mac)
                    except ValueError:
                        continue
                    candidate = self.dr.async_get_device(connections={(dr.CONNECTION_NETWORK_MAC, alt_mac_norm)})
                    if (
                        candidate is not None
                        and candidate.identifiers
                        and not any(c[0] == "bluetooth" for c in candidate.connections)
                    ):
                        congealment_device = candidate
                        break

            # Priority 3: Fall back to entry_id (may be BT or ESPHome).
            # This is the last resort — entry_id often points to the BT
            # device, which is acceptable if no ESPHome device was found.
            if congealment_device is None and self._device.entry_id:
                congealment_device = self.dr.async_get(self._device.entry_id)

            if congealment_device is not None and congealment_device.identifiers:
                return DeviceInfo(
                    identifiers=congealment_device.identifiers,
                    name=self._device.name,
                )
            # Fallback: use connections for congealment if all lookups fail.
            # Only add CONNECTION_NETWORK_MAC if we actually know the WiFi MAC.
            # Using the BLE MAC as CONNECTION_NETWORK_MAC is incorrect and
            # would create a separate device instead of congealing.
            connections = set()
            if self._device.address_wifi_mac and is_mac_address(self._device.address_wifi_mac):
                connections.add((dr.CONNECTION_NETWORK_MAC, normalize_mac(self._device.address_wifi_mac)))
            ble_address = self._device.address_ble_mac or self._device.address
            if ble_address and is_mac_address(ble_address):
                connections.add((dr.CONNECTION_BLUETOOTH, normalize_mac(ble_address)))
        elif self._device.address_type == ADDR_TYPE_IBEACON:
            # ibeacon doesn't (yet) actually set a "connection", but
            # this "matches" what it stores for identifier.
            connections = {("ibeacon", self._device.address.lower())}
            model = f"iBeacon: {self._device.address.lower()}"
        elif self._device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
            # Private BLE Device integration doesn't specify "connection" tuples,
            # so we use what it defines for the "identifier" instead.
            connections = {("private_ble_device", self._device.address.lower())}
            # We don't set the model since the Private BLE integration should have
            # already named it nicely.
            # model = f"IRK: {self._device.address.lower()[:4]}"
            # We look up and use the device from the registry so we get
            # the private_ble_device device congealment!
            # The "connection" is actually being used as the "identifiers" tuple
            # here.
            # dr_device = self.devreg.async_get_device(connection)
            # if dr_device is not None:
            #    existing_device_id = dr_device.id
            domain_name = DOMAIN_PRIVATE_BLE_DEVICE
        elif self._device.address_type == ADDR_TYPE_FMDN_DEVICE:
            # FMDN Device (Google Find My Device Network) - use the device_id
            # from googlefindmy integration to enable device congealment.
            # Bermuda entities will appear in the googlefindmy device.
            #
            # To properly congeal with googlefindmy, we need to use the SAME
            # identifiers that googlefindmy uses. We get these from the device
            # registry entry.
            if self._device.fmdn_device_id:
                fmdn_device_entry = self.dr.async_get(self._device.fmdn_device_id)
                if fmdn_device_entry and fmdn_device_entry.identifiers:
                    # Use the same identifiers as googlefindmy for proper congealment
                    # This makes Bermuda entities appear under the same device as
                    # googlefindmy entities, showing both integrations in the device info.
                    return DeviceInfo(
                        identifiers=fmdn_device_entry.identifiers,
                        name=self._device.name,
                    )
                # Fallback: create identifier using canonical_id
                connections = set()
            else:
                connections = set()
            # We don't set the model since the googlefindmy integration should have
            # already named it nicely.
            domain_name = DOMAIN_GOOGLEFINDMY
        elif is_mac_address(self._device.address):
            connections = {(dr.CONNECTION_BLUETOOTH, normalize_mac(self._device.address))}
        else:
            connections = set()
            # No need to set model, since MAC address will be shown via connection.
            # model = f"Bermuda: {self._device.address.lower()}"

        # For FMDN devices, use canonical_id (without fmdn: prefix) to match
        # googlefindmy's identifier format. This helps with congealment in fallback cases.
        identifier_value: str
        if self._device.address_type == ADDR_TYPE_FMDN_DEVICE and self._device.fmdn_canonical_id:
            identifier_value = self._device.fmdn_canonical_id
        elif self._device.unique_id is not None:
            identifier_value = self._device.unique_id
        else:
            identifier_value = self._device.address

        return DeviceInfo(
            identifiers={(domain_name, identifier_value)},
            connections=connections,
            name=self._device.name,
            model=model,
        )

    @property
    def device_state_attributes(self) -> dict[str, str]:
        """Return the state attributes."""
        return {
            "attribution": ATTRIBUTION,
            "id": str(self.coordinator.data.get("id")),
            "integration": DOMAIN,
        }


class BermudaGlobalEntity(CoordinatorEntity):
    """Holds all Bermuda global data under one entity type/device."""

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._cache_ratelimit_value = None
        self._cache_ratelimit_stamp: float = 0
        self._cache_ratelimit_interval = 60

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the co-ordinator.

        (we don't need to implement this, but if we want to do anything special we can)
        """
        self.async_write_ha_state()

    def _cached_ratelimit(self, statevalue: Any, interval: int | None = None) -> Any:
        """A simple way to rate-limit sensor updates."""
        if interval is not None:
            self._cache_ratelimit_interval = interval
        nowstamp = monotonic_time_coarse()

        if nowstamp > self._cache_ratelimit_stamp + self._cache_ratelimit_interval:
            self._cache_ratelimit_stamp = nowstamp
            self._cache_ratelimit_value = statevalue
            return statevalue
        else:
            return self._cache_ratelimit_value

    @property
    def device_info(self) -> DeviceInfo | None:
        """Implementing this creates an entry in the device registry."""
        return DeviceInfo(
            identifiers={(DOMAIN, "BERMUDA_GLOBAL")},
            name="Bermuda Global",
        )
