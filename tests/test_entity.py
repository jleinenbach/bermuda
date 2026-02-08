"""Test Bermuda entity classes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import (
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
from custom_components.bermuda.entity import BermudaEntity, BermudaGlobalEntity


class TestBermudaEntityInit:
    """Tests for BermudaEntity initialization."""

    def _create_entity(
        self,
        address: str = "aa:bb:cc:dd:ee:ff",
        update_interval: float | None = None,
    ) -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = address
        mock_coordinator.devices = {address: mock_device}

        mock_config_entry = MagicMock()
        if update_interval is not None:
            mock_config_entry.options = {CONF_UPDATE_INTERVAL: update_interval}
        else:
            mock_config_entry.options = {}

        # Create the entity without calling __init__ to avoid CoordinatorEntity complexity
        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            entity = object.__new__(BermudaEntity)
            entity.coordinator = mock_coordinator
            entity.config_entry = mock_config_entry
            entity.address = address
            entity._device = mock_device
            entity._lastname = mock_device.name
            entity.ar = mock_ar.return_value
            entity.dr = mock_dr.return_value
            entity.devreg_init_done = False
            entity.bermuda_update_interval = mock_config_entry.options.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            )
            entity.bermuda_last_state = 0
            entity.bermuda_last_stamp = 0

        return entity

    def test_entity_stores_address(self) -> None:
        """Test that entity stores the address correctly."""
        address = "aa:bb:cc:dd:ee:ff"
        entity = self._create_entity(address=address)

        assert entity.address == address

    def test_entity_stores_device_reference(self) -> None:
        """Test that entity stores reference to device."""
        entity = self._create_entity()

        assert entity._device is not None
        assert entity._device.name == "Test Device"

    def test_entity_uses_default_update_interval(self) -> None:
        """Test that entity uses default update interval when not configured."""
        entity = self._create_entity()

        assert entity.bermuda_update_interval == DEFAULT_UPDATE_INTERVAL

    def test_entity_uses_configured_update_interval(self) -> None:
        """Test that entity uses configured update interval."""
        entity = self._create_entity(update_interval=5.0)

        assert entity.bermuda_update_interval == 5.0

    def test_entity_initializes_rate_limit_state(self) -> None:
        """Test that entity initializes rate limit state."""
        entity = self._create_entity()

        assert entity.bermuda_last_state == 0
        assert entity.bermuda_last_stamp == 0

    def test_devreg_init_done_starts_false(self) -> None:
        """Test that devreg_init_done starts as False."""
        entity = self._create_entity()

        assert entity.devreg_init_done is False


class TestBermudaEntityUniqueId:
    """Tests for BermudaEntity unique_id property."""

    def _create_entity(self, unique_id: str | None = "test_unique_id") -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_coordinator = MagicMock()
        mock_device = MagicMock()
        mock_device.unique_id = unique_id
        mock_coordinator.devices = {"test_address": mock_device}

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device

        return entity

    def test_unique_id_returns_device_unique_id(self) -> None:
        """Test that unique_id returns device's unique_id."""
        entity = self._create_entity(unique_id="my_unique_id")

        assert entity.unique_id == "my_unique_id"

    def test_unique_id_returns_none_when_device_has_none(self) -> None:
        """Test that unique_id returns None when device has None."""
        entity = self._create_entity(unique_id=None)

        assert entity.unique_id is None


class TestBermudaEntityDeviceInfo:
    """Tests for BermudaEntity device_info property."""

    def _create_entity(
        self,
        address: str = "aa:bb:cc:dd:ee:ff",
        address_type: str | None = None,
        is_scanner: bool = False,
        scanner_entity: bool = False,
        unique_id: str = "test_unique_id",
        fmdn_device_id: str | None = None,
        fmdn_canonical_id: str | None = None,
        entry_id: str | None = None,
        address_wifi_mac: str | None = None,
        address_ble_mac: str | None = None,
    ) -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = unique_id
        mock_device.address = address
        mock_device.address_type = address_type
        mock_device.is_scanner = is_scanner
        mock_device.address_wifi_mac = address_wifi_mac
        mock_device.address_ble_mac = address_ble_mac
        mock_device.fmdn_device_id = fmdn_device_id
        mock_device.fmdn_canonical_id = fmdn_canonical_id
        mock_device.entry_id = entry_id

        mock_dr = MagicMock()
        mock_dr.async_get.return_value = None
        mock_dr.async_get_device.return_value = None

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device
        entity.dr = mock_dr
        entity._scanner_entity = scanner_entity

        return entity

    def test_device_info_for_regular_mac_device(self) -> None:
        """Test device_info for regular MAC address device."""
        entity = self._create_entity(address="aa:bb:cc:dd:ee:ff")

        device_info = entity.device_info

        assert device_info is not None
        assert (DOMAIN, "test_unique_id") in device_info["identifiers"]
        assert device_info["name"] == "Test Device"

    def test_device_info_for_ibeacon(self) -> None:
        """Test device_info for iBeacon device."""
        entity = self._create_entity(
            address="uuid_major_minor",
            address_type=ADDR_TYPE_IBEACON,
        )

        device_info = entity.device_info

        assert device_info is not None
        assert ("ibeacon", "uuid_major_minor") in device_info["connections"]
        assert device_info["model"] == "iBeacon: uuid_major_minor"

    def test_device_info_for_private_ble_device(self) -> None:
        """Test device_info for Private BLE Device."""
        entity = self._create_entity(
            address="irk_address",
            address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE,
        )

        device_info = entity.device_info

        assert device_info is not None
        assert (DOMAIN_PRIVATE_BLE_DEVICE, "test_unique_id") in device_info["identifiers"]

    def test_device_info_for_fmdn_device_with_congealment(self) -> None:
        """Test device_info for FMDN device with successful congealment."""
        entity = self._create_entity(
            address="fmdn:canonical_id",
            address_type=ADDR_TYPE_FMDN_DEVICE,
            fmdn_device_id="fmdn_device_id",
            fmdn_canonical_id="canonical_id",
        )

        # Mock the device registry entry
        mock_fmdn_entry = MagicMock()
        mock_fmdn_entry.identifiers = {(DOMAIN_GOOGLEFINDMY, "fmdn_id")}
        entity.dr.async_get.return_value = mock_fmdn_entry

        device_info = entity.device_info

        assert device_info is not None
        assert (DOMAIN_GOOGLEFINDMY, "fmdn_id") in device_info["identifiers"]
        assert device_info["name"] == "Test Device"

    def test_device_info_for_fmdn_device_without_congealment(self) -> None:
        """Test device_info for FMDN device without congealment (fallback)."""
        entity = self._create_entity(
            address="fmdn:canonical_id",
            address_type=ADDR_TYPE_FMDN_DEVICE,
            fmdn_device_id=None,
            fmdn_canonical_id="canonical_id",
        )

        device_info = entity.device_info

        assert device_info is not None
        assert (DOMAIN_GOOGLEFINDMY, "canonical_id") in device_info["identifiers"]

    def test_device_info_for_scanner(self) -> None:
        """Test device_info for scanner device without entry_id falls back to connections."""
        entity = self._create_entity(
            address="aa:bb:cc:dd:ee:ff",
            is_scanner=True,
            scanner_entity=True,
            entry_id=None,
        )

        device_info = entity.device_info

        assert device_info is not None
        # Scanner without entry_id should fall back to bluetooth connections
        assert len(device_info["connections"]) > 0

    def test_device_info_for_scanner_wifi_mac_priority(self) -> None:
        """Test scanner congealment prefers ESPHome device via WiFi MAC.

        When a scanner has address_wifi_mac, it should look up the ESPHome/Shelly
        device via CONNECTION_NETWORK_MAC first (Priority 1), before falling back
        to MAC offset (Priority 2) or entry_id (Priority 3). This ensures
        congealment targets the ESPHome device (with firmware info, manufacturer
        etc.) rather than the HA Bluetooth auto-created device.
        """
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            entry_id="bt_device_registry_id",
            address_wifi_mac="48:27:e2:e3:f2:da",
        )

        # WiFi MAC lookup returns the ESPHome device (no bluetooth connection)
        mock_esphome_entry = MagicMock()
        mock_esphome_entry.identifiers = {("esphome", "atoms3-bt-5")}
        mock_esphome_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:da")})
        entity.dr.async_get_device.return_value = mock_esphome_entry

        device_info = entity.device_info

        assert device_info is not None
        # Should use ESPHome identifiers (from WiFi MAC lookup)
        assert ("esphome", "atoms3-bt-5") in device_info["identifiers"]
        assert device_info["name"] == "Test Device"

    def test_device_info_for_scanner_fallback_to_entry_id(self) -> None:
        """Test scanner congealment falls back to entry_id when no WiFi MAC and no MAC offset match."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:da",
            is_scanner=True,
            scanner_entity=True,
            entry_id="scanner_device_registry_id",
            address_wifi_mac=None,
        )

        # MAC offset search returns None (no device found)
        entity.dr.async_get_device.return_value = None

        # entry_id lookup returns ESPHome device
        mock_entry = MagicMock()
        mock_entry.identifiers = {("esphome", "atoms3-bt-5")}
        mock_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:da")})
        entity.dr.async_get.return_value = mock_entry

        device_info = entity.device_info

        assert device_info is not None
        assert ("esphome", "atoms3-bt-5") in device_info["identifiers"]
        entity.dr.async_get.assert_called_with("scanner_device_registry_id")

    def test_device_info_for_scanner_fallback_wifi_mac_not_found(self) -> None:
        """Test scanner congealment falls back to entry_id when WiFi MAC and MAC offset fail."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            entry_id="bt_device_registry_id",
            address_wifi_mac="48:27:e2:e3:f2:da",
        )

        # WiFi MAC lookup AND MAC offset search both return None
        entity.dr.async_get_device.return_value = None

        # Fall back to entry_id (Priority 3)
        mock_bt_entry = MagicMock()
        mock_bt_entry.identifiers = {("bluetooth", "48:27:E2:E3:F2:D8")}
        mock_bt_entry.connections = frozenset({("bluetooth", "48:27:e2:e3:f2:d8")})
        entity.dr.async_get.return_value = mock_bt_entry

        device_info = entity.device_info

        assert device_info is not None
        # Should use entry_id fallback
        assert ("bluetooth", "48:27:E2:E3:F2:D8") in device_info["identifiers"]
        entity.dr.async_get.assert_called_with("bt_device_registry_id")

    def test_device_info_for_scanner_congealment_fallback_no_entry(self) -> None:
        """Test scanner congealment falls back to connections when all lookups fail."""
        entity = self._create_entity(
            address="aa:bb:cc:dd:ee:ff",
            is_scanner=True,
            scanner_entity=True,
            entry_id="nonexistent_entry_id",
        )

        # Both lookups return None
        entity.dr.async_get_device.return_value = None
        entity.dr.async_get.return_value = None

        device_info = entity.device_info

        assert device_info is not None
        # Should fall back to connections-based approach
        assert len(device_info["connections"]) > 0
        assert (DOMAIN, "test_unique_id") in device_info["identifiers"]

    def test_device_info_for_scanner_congealment_fallback_no_identifiers(self) -> None:
        """Test scanner congealment falls back when entry has no identifiers."""
        entity = self._create_entity(
            address="aa:bb:cc:dd:ee:ff",
            is_scanner=True,
            scanner_entity=True,
            entry_id="entry_with_no_identifiers",
        )

        entity.dr.async_get_device.return_value = None

        mock_entry = MagicMock()
        mock_entry.identifiers = set()  # Empty identifiers
        entity.dr.async_get.return_value = mock_entry

        device_info = entity.device_info

        assert device_info is not None
        # Should fall back to connections-based approach
        assert len(device_info["connections"]) > 0
        assert (DOMAIN, "test_unique_id") in device_info["identifiers"]

    def test_device_info_for_scanner_congealment_preserves_name(self) -> None:
        """Test scanner congealment preserves the Bermuda device name."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:da",
            is_scanner=True,
            scanner_entity=True,
            address_wifi_mac="48:27:e2:e3:f2:da",
        )
        entity._device.name = "BT Scanner 5 Wohnzimmer"

        mock_entry = MagicMock()
        mock_entry.identifiers = {("esphome", "atoms3-bt-5")}
        mock_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:da")})
        entity.dr.async_get_device.return_value = mock_entry

        device_info = entity.device_info

        assert device_info is not None
        assert device_info["name"] == "BT Scanner 5 Wohnzimmer"

    def test_device_info_scanner_congealment_with_multiple_identifiers(self) -> None:
        """Test scanner congealment works with multiple native identifiers."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            address_wifi_mac="48:27:e2:e3:f2:da",
        )

        # Some integrations register multiple identifiers
        mock_entry = MagicMock()
        mock_entry.identifiers = {
            ("esphome", "atoms3-bt-5"),
            ("esphome", "atoms3-bt-5-bluetooth"),
        }
        mock_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:da")})
        entity.dr.async_get_device.return_value = mock_entry

        device_info = entity.device_info

        assert device_info is not None
        # All native identifiers should be passed through
        assert ("esphome", "atoms3-bt-5") in device_info["identifiers"]
        assert ("esphome", "atoms3-bt-5-bluetooth") in device_info["identifiers"]

    def test_device_info_scanner_wifi_mac_normalizes_address(self) -> None:
        """Test that WiFi MAC is normalized before device registry lookup."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            address_wifi_mac="48:27:E2:E3:F2:DA",  # Uppercase
        )

        mock_entry = MagicMock()
        mock_entry.identifiers = {("esphome", "test")}
        mock_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:da")})
        entity.dr.async_get_device.return_value = mock_entry

        entity.device_info

        # Verify the MAC was normalized (lowercase, colon-delimited)
        call_args = entity.dr.async_get_device.call_args
        connections = call_args.kwargs.get("connections") or call_args[1].get("connections")
        assert connections is not None
        for conn_type, mac in connections:
            assert mac == "48:27:e2:e3:f2:da"  # Normalized lowercase

    def test_device_info_scanner_priority2_mac_offset_finds_esphome(self) -> None:
        """Test Priority 2: MAC-offset search finds ESPHome device when P1 fails.

        When address_wifi_mac is None, the code searches MAC offsets (-3 to +2)
        from the BLE address to find an ESPHome/Shelly device registered with
        CONNECTION_NETWORK_MAC. Candidates with "bluetooth" connections are skipped.
        """
        # BLE address: offset -2 from WiFi MAC (typical ESP32)
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            entry_id=None,
            address_wifi_mac=None,  # WiFi MAC not resolved
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # ESPHome device registered with WiFi MAC (offset -2 from BLE)
        mock_esphome_entry = MagicMock()
        mock_esphome_entry.identifiers = {("esphome", "atoms3-bt-5")}
        mock_esphome_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:d6")})

        def mock_get_device(connections=None, identifiers=None):
            if connections:
                for conn_type, mac in connections:
                    # WiFi MAC = BLE MAC - 2 = 48:27:e2:e3:f2:d6
                    if conn_type == "mac" and mac == "48:27:e2:e3:f2:d6":
                        return mock_esphome_entry
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        device_info = entity.device_info

        assert device_info is not None
        assert ("esphome", "atoms3-bt-5") in device_info["identifiers"]
        assert device_info["name"] == "Test Device"

    def test_device_info_scanner_priority2_uses_address_ble_mac(self) -> None:
        """Test Priority 2: uses address_ble_mac when available for offset search."""
        entity = self._create_entity(
            address="some:other:addr:00:00:01",
            is_scanner=True,
            scanner_entity=True,
            entry_id=None,
            address_wifi_mac=None,
            address_ble_mac="48:27:e2:e3:f2:d8",  # BLE MAC set separately
        )

        mock_esphome_entry = MagicMock()
        mock_esphome_entry.identifiers = {("esphome", "my-scanner")}
        mock_esphome_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:d6")})

        def mock_get_device(connections=None, identifiers=None):
            if connections:
                for conn_type, mac in connections:
                    # WiFi MAC = BLE MAC - 2
                    if conn_type == "mac" and mac == "48:27:e2:e3:f2:d6":
                        return mock_esphome_entry
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        device_info = entity.device_info

        assert device_info is not None
        assert ("esphome", "my-scanner") in device_info["identifiers"]

    def test_device_info_scanner_priority2_skips_no_identifiers(self) -> None:
        """Test Priority 2: skips candidates without identifiers and keeps searching."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            entry_id=None,
            address_wifi_mac=None,
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # First candidate has no identifiers, second one does
        mock_empty = MagicMock()
        mock_empty.identifiers = set()
        mock_empty.connections = frozenset({("mac", "48:27:e2:e3:f2:d5")})
        mock_good = MagicMock()
        mock_good.identifiers = {("esphome", "found-it")}
        mock_good.connections = frozenset({("mac", "48:27:e2:e3:f2:d6")})

        call_count = 0

        def mock_get_device(connections=None, identifiers=None):
            nonlocal call_count
            if connections:
                for conn_type, mac in connections:
                    if conn_type == "mac":
                        call_count += 1
                        # First match: empty identifiers (offset -3)
                        if mac == "48:27:e2:e3:f2:d5":
                            return mock_empty
                        # Second match: good identifiers (offset -2)
                        if mac == "48:27:e2:e3:f2:d6":
                            return mock_good
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        device_info = entity.device_info

        assert device_info is not None
        assert ("esphome", "found-it") in device_info["identifiers"]

    def test_device_info_scanner_priority2_finds_before_entry_id(self) -> None:
        """Test Priority 2 (MAC offset) finds ESPHome before Priority 3 (entry_id)."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            entry_id="bt_entry_id",
            address_wifi_mac=None,
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # Priority 2 finds ESPHome device via MAC offset
        mock_esphome_entry = MagicMock()
        mock_esphome_entry.identifiers = {("esphome", "my-esp")}
        mock_esphome_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:d6")})

        def mock_get_device(connections=None, identifiers=None):
            if connections:
                for conn_type, mac in connections:
                    if conn_type == "mac" and mac == "48:27:e2:e3:f2:d6":
                        return mock_esphome_entry
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        device_info = entity.device_info

        assert device_info is not None
        # Should use ESPHome (from MAC offset) as primary
        assert ("esphome", "my-esp") in device_info["identifiers"]

    def test_device_info_scanner_fallback_no_wifi_mac_no_network_mac_connection(self) -> None:
        """Test fallback: when WiFi MAC is None, CONNECTION_NETWORK_MAC is NOT added.

        Previously the fallback used the BLE MAC for CONNECTION_NETWORK_MAC which
        is incorrect (BLE MAC != WiFi MAC) and would create a separate device.
        """
        entity = self._create_entity(
            address="aa:bb:cc:dd:ee:ff",
            is_scanner=True,
            scanner_entity=True,
            entry_id=None,
            address_wifi_mac=None,
        )

        # All lookups fail
        entity.dr.async_get_device.return_value = None
        entity.dr.async_get.return_value = None

        device_info = entity.device_info

        assert device_info is not None
        # Check that CONNECTION_NETWORK_MAC is NOT in connections
        for conn_type, _mac in device_info["connections"]:
            assert conn_type != "mac", "CONNECTION_NETWORK_MAC should not be set when WiFi MAC is unknown"
        # CONNECTION_BLUETOOTH should still be present
        bluetooth_conns = [(ct, m) for ct, m in device_info["connections"] if ct == "bluetooth"]
        assert len(bluetooth_conns) == 1

    def test_device_info_scanner_fallback_with_wifi_mac_has_both_connections(self) -> None:
        """Test fallback: when WiFi MAC is known, both connection types are added."""
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            is_scanner=True,
            scanner_entity=True,
            entry_id=None,
            address_wifi_mac="48:27:e2:e3:f2:da",
        )

        # WiFi MAC lookup fails (device not in registry yet)
        entity.dr.async_get_device.return_value = None
        entity.dr.async_get.return_value = None

        device_info = entity.device_info

        assert device_info is not None
        conn_types = {ct for ct, _m in device_info["connections"]}
        assert "mac" in conn_types, "CONNECTION_NETWORK_MAC should be set when WiFi MAC is known"
        assert "bluetooth" in conn_types, "CONNECTION_BLUETOOTH should be present"
        # Verify the MAC values
        conn_dict = {ct: m for ct, m in device_info["connections"]}
        assert conn_dict["mac"] == "48:27:e2:e3:f2:da"
        assert conn_dict["bluetooth"] == "48:27:e2:e3:f2:d8"


class TestScannerPollutionFix:
    """Tests for scanner device congealment pollution fix.

    The root cause: old Bermuda code added CONNECTION_NETWORK_MAC with the
    BLE MAC address to BT devices via async_get_or_create(). HA merged this
    into the BT device, creating a stale "mac" connection. This caused:
    1. BT device classified as BOTH scanner_devreg_bt AND scanner_devreg_mac
    2. address_wifi_mac set to BLE MAC (wrong)
    3. entity.py congealed with BT device instead of ESPHome device
    """

    def _create_entity(
        self,
        address: str = "48:27:e2:e3:f2:d8",
        entry_id: str | None = None,
        address_wifi_mac: str | None = None,
        address_ble_mac: str | None = None,
    ) -> BermudaEntity:
        """Create a scanner BermudaEntity for testing."""
        mock_device = MagicMock()
        mock_device.name = "Test Scanner"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = address
        mock_device.address_type = None
        mock_device.is_scanner = True
        mock_device.address_wifi_mac = address_wifi_mac
        mock_device.address_ble_mac = address_ble_mac
        mock_device.fmdn_device_id = None
        mock_device.fmdn_canonical_id = None
        mock_device.entry_id = entry_id

        mock_dr = MagicMock()
        mock_dr.async_get.return_value = None
        mock_dr.async_get_device.return_value = None

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device
        entity.dr = mock_dr
        entity._scanner_entity = True

        return entity

    def test_p1_wifi_mac_skips_polluted_bt_device(self) -> None:
        """Priority 1: WiFi MAC lookup skips BT device with polluted 'mac' connection.

        When old Bermuda code added a 'mac' connection to a BT device, a WiFi MAC
        lookup might find that BT device. The fix verifies the result has no
        'bluetooth' connection before accepting it.
        """
        entity = self._create_entity(
            address_wifi_mac="48:27:e2:e3:f2:d8",  # Actually the BLE MAC (wrong)
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # WiFi MAC lookup finds the POLLUTED BT device
        mock_polluted_bt = MagicMock()
        mock_polluted_bt.identifiers = {("bluetooth", "48:27:e2:e3:f2:d8")}
        mock_polluted_bt.connections = frozenset(
            {
                ("bluetooth", "48:27:e2:e3:f2:d8"),
                ("mac", "48:27:e2:e3:f2:d8"),  # POLLUTED!
            }
        )

        entity.dr.async_get_device.return_value = mock_polluted_bt

        device_info = entity.device_info

        assert device_info is not None
        # Should NOT use the polluted BT device identifiers
        assert ("bluetooth", "48:27:e2:e3:f2:d8") not in device_info.get("identifiers", set())

    def test_p2_mac_offset_skips_polluted_bt_device(self) -> None:
        """Priority 2: MAC offset search skips BT device with 'bluetooth' connection.

        When searching MAC offsets, a BT device with a polluted 'mac' connection
        at offset 0 should be skipped in favor of the real ESPHome device at offset -2.
        """
        entity = self._create_entity(
            address_wifi_mac=None,
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # Polluted BT device found at offset 0
        mock_polluted_bt = MagicMock()
        mock_polluted_bt.identifiers = {("bluetooth", "48:27:e2:e3:f2:d8")}
        mock_polluted_bt.connections = frozenset(
            {
                ("bluetooth", "48:27:e2:e3:f2:d8"),
                ("mac", "48:27:e2:e3:f2:d8"),  # POLLUTED!
            }
        )

        # Real ESPHome device found at offset -2
        mock_esphome = MagicMock()
        mock_esphome.identifiers = {("esphome", "my-scanner")}
        mock_esphome.connections = frozenset({("mac", "48:27:e2:e3:f2:d6")})

        def mock_get_device(connections=None, identifiers=None):
            if connections:
                for conn_type, mac in connections:
                    if conn_type == "mac":
                        if mac == "48:27:e2:e3:f2:d8":
                            return mock_polluted_bt
                        if mac == "48:27:e2:e3:f2:d6":
                            return mock_esphome
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        device_info = entity.device_info

        assert device_info is not None
        # Should use ESPHome (skipped polluted BT), NOT bluetooth
        assert ("esphome", "my-scanner") in device_info["identifiers"]

    def test_full_pollution_scenario_esphome_at_offset(self) -> None:
        """Full scenario: BT device polluted, ESPHome found via MAC offset.

        This tests the complete fix:
        1. address_wifi_mac is BLE MAC (wrong, from polluted classification)
        2. P1 finds polluted BT device → SKIPPED (has 'bluetooth' connection)
        3. P2 MAC offset finds real ESPHome at offset -2 → USED
        """
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            entry_id="bt_entry_id",
            address_wifi_mac="48:27:e2:e3:f2:d8",  # Wrong: BLE MAC as WiFi MAC
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # Polluted BT device
        mock_polluted_bt = MagicMock()
        mock_polluted_bt.identifiers = {("bluetooth", "48:27:e2:e3:f2:d8")}
        mock_polluted_bt.connections = frozenset(
            {
                ("bluetooth", "48:27:e2:e3:f2:d8"),
                ("mac", "48:27:e2:e3:f2:d8"),
            }
        )

        # Real ESPHome device (WiFi MAC = BLE - 2)
        mock_esphome = MagicMock()
        mock_esphome.identifiers = {("esphome", "my-scanner")}
        mock_esphome.connections = frozenset({("mac", "48:27:e2:e3:f2:d6")})

        def mock_get_device(connections=None, identifiers=None):
            if connections:
                for conn_type, mac in connections:
                    if conn_type == "mac":
                        if mac == "48:27:e2:e3:f2:d8":
                            return mock_polluted_bt
                        if mac == "48:27:e2:e3:f2:d6":
                            return mock_esphome
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        device_info = entity.device_info

        assert device_info is not None
        # Should congeal with ESPHome as primary
        assert ("esphome", "my-scanner") in device_info["identifiers"]

    def test_p3_entry_id_used_when_no_esphome_found(self) -> None:
        """Priority 3: entry_id used as last resort when no ESPHome/Shelly found.

        When MAC offset search finds only polluted BT devices and no ESPHome,
        the entry_id lookup is the last resort.
        """
        entity = self._create_entity(
            address="48:27:e2:e3:f2:d8",
            entry_id="bt_entry_id",
            address_wifi_mac=None,
            address_ble_mac="48:27:e2:e3:f2:d8",
        )

        # Only polluted BT device found via MAC offset
        mock_polluted_bt = MagicMock()
        mock_polluted_bt.identifiers = {("bluetooth", "48:27:e2:e3:f2:d8")}
        mock_polluted_bt.connections = frozenset(
            {
                ("bluetooth", "48:27:e2:e3:f2:d8"),
                ("mac", "48:27:e2:e3:f2:d8"),
            }
        )

        def mock_get_device(connections=None, identifiers=None):
            if connections:
                for conn_type, mac in connections:
                    if conn_type == "mac" and mac == "48:27:e2:e3:f2:d8":
                        return mock_polluted_bt
            return None

        entity.dr.async_get_device.side_effect = mock_get_device

        # entry_id returns BT device as last resort
        mock_entry = MagicMock()
        mock_entry.identifiers = {("bluetooth", "48:27:e2:e3:f2:d8")}
        entity.dr.async_get.return_value = mock_entry

        device_info = entity.device_info

        assert device_info is not None
        # Falls back to entry_id (the BT device is all we have)
        assert ("bluetooth", "48:27:e2:e3:f2:d8") in device_info["identifiers"]
        entity.dr.async_get.assert_called_with("bt_entry_id")


class TestBermudaEntityDeviceStateAttributes:
    """Tests for BermudaEntity device_state_attributes property."""

    def _create_entity(self) -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.data = {"id": "test_id"}

        entity = object.__new__(BermudaEntity)
        entity.coordinator = mock_coordinator

        return entity

    def test_device_state_attributes_contains_attribution(self) -> None:
        """Test that device_state_attributes contains attribution."""
        entity = self._create_entity()

        attrs = entity.device_state_attributes

        assert attrs["attribution"] == ATTRIBUTION

    def test_device_state_attributes_contains_id(self) -> None:
        """Test that device_state_attributes contains id."""
        entity = self._create_entity()

        attrs = entity.device_state_attributes

        assert attrs["id"] == "test_id"

    def test_device_state_attributes_contains_integration(self) -> None:
        """Test that device_state_attributes contains integration."""
        entity = self._create_entity()

        attrs = entity.device_state_attributes

        assert attrs["integration"] == DOMAIN


class TestBermudaEntityCachedRatelimit:
    """Tests for BermudaEntity _cached_ratelimit method."""

    def _create_entity(self, update_interval: float = 1.0) -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_device = MagicMock()
        mock_device.ref_power_changed = 0

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device
        entity.bermuda_update_interval = update_interval
        entity.bermuda_last_state = None
        entity.bermuda_last_stamp = 0

        return entity

    def test_cached_ratelimit_returns_new_value_when_cache_empty(self) -> None:
        """Test that _cached_ratelimit returns new value when cache is empty."""
        entity = self._create_entity()

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=100.0,
        ):
            result = entity._cached_ratelimit(42.0)

        assert result == 42.0
        assert entity.bermuda_last_state == 42.0

    def test_cached_ratelimit_returns_cached_value_when_not_stale(self) -> None:
        """Test that _cached_ratelimit returns cached value when not stale."""
        entity = self._create_entity(update_interval=10.0)
        entity.bermuda_last_state = 50.0
        entity.bermuda_last_stamp = 100.0

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=105.0,  # Only 5 seconds elapsed, interval is 10
        ):
            # fast_falling=False to test pure caching behavior without fast_falling shortcut
            result = entity._cached_ratelimit(42.0, fast_falling=False)

        assert result == 50.0  # Cached value

    def test_cached_ratelimit_returns_new_value_when_stale(self) -> None:
        """Test that _cached_ratelimit returns new value when cache is stale."""
        entity = self._create_entity(update_interval=10.0)
        entity.bermuda_last_state = 50.0
        entity.bermuda_last_stamp = 100.0

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=115.0,  # 15 seconds elapsed, interval is 10
        ):
            result = entity._cached_ratelimit(42.0)

        assert result == 42.0  # New value

    def test_cached_ratelimit_fast_falling_returns_lower_value(self) -> None:
        """Test that _cached_ratelimit with fast_falling returns lower values immediately."""
        entity = self._create_entity(update_interval=10.0)
        entity.bermuda_last_state = 50.0
        entity.bermuda_last_stamp = 100.0

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=102.0,  # Only 2 seconds elapsed
        ):
            result = entity._cached_ratelimit(30.0, fast_falling=True)

        assert result == 30.0  # New lower value returned immediately

    def test_cached_ratelimit_fast_rising_returns_higher_value(self) -> None:
        """Test that _cached_ratelimit with fast_rising returns higher values immediately."""
        entity = self._create_entity(update_interval=10.0)
        entity.bermuda_last_state = 50.0
        entity.bermuda_last_stamp = 100.0

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=102.0,  # Only 2 seconds elapsed
        ):
            result = entity._cached_ratelimit(70.0, fast_rising=True)

        assert result == 70.0  # New higher value returned immediately

    def test_cached_ratelimit_uses_custom_interval(self) -> None:
        """Test that _cached_ratelimit uses custom interval when provided."""
        entity = self._create_entity(update_interval=10.0)

        entity._cached_ratelimit(42.0, interval=5.0)

        assert entity.bermuda_update_interval == 5.0


class TestBermudaEntityHandleCoordinatorUpdate:
    """Tests for BermudaEntity _handle_coordinator_update method."""

    def _create_entity(self) -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"

        mock_dr = MagicMock()

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device
        entity._lastname = "Test Device"
        entity.dr = mock_dr
        entity.devreg_init_done = False
        entity.device_entry = None
        entity.async_write_ha_state = MagicMock()

        return entity

    def test_handle_coordinator_update_calls_async_write_ha_state(self) -> None:
        """Test that _handle_coordinator_update calls async_write_ha_state."""
        entity = self._create_entity()

        entity._handle_coordinator_update()

        entity.async_write_ha_state.assert_called_once()

    def test_handle_coordinator_update_updates_device_registry_on_name_change(self) -> None:
        """Test that _handle_coordinator_update updates device registry when name changes."""
        entity = self._create_entity()
        entity._device.name = "New Name"
        entity._lastname = "Old Name"
        entity.device_entry = MagicMock()
        entity.device_entry.id = "device_id"

        entity._handle_coordinator_update()

        entity.dr.async_update_device.assert_called_once()
        assert entity._lastname == "New Name"


class TestBermudaGlobalEntity:
    """Tests for BermudaGlobalEntity class."""

    def _create_global_entity(self) -> BermudaGlobalEntity:
        """Create a BermudaGlobalEntity instance for testing."""
        mock_coordinator = MagicMock()
        mock_config_entry = MagicMock()

        entity = object.__new__(BermudaGlobalEntity)
        entity.coordinator = mock_coordinator
        entity.config_entry = mock_config_entry
        entity._cache_ratelimit_value = None
        entity._cache_ratelimit_stamp = 0
        entity._cache_ratelimit_interval = 60
        entity.async_write_ha_state = MagicMock()

        return entity

    def test_global_entity_device_info(self) -> None:
        """Test that global entity has correct device_info."""
        entity = self._create_global_entity()

        device_info = entity.device_info

        assert device_info is not None
        assert (DOMAIN, "BERMUDA_GLOBAL") in device_info["identifiers"]
        assert device_info["name"] == "Bermuda Global"

    def test_global_entity_handle_coordinator_update(self) -> None:
        """Test that global entity calls async_write_ha_state on update."""
        entity = self._create_global_entity()

        entity._handle_coordinator_update()

        entity.async_write_ha_state.assert_called_once()

    def test_global_entity_cached_ratelimit_returns_new_value_first_time(self) -> None:
        """Test that global entity _cached_ratelimit returns new value first time."""
        entity = self._create_global_entity()

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=100.0,
        ):
            result = entity._cached_ratelimit(42)

        assert result == 42
        assert entity._cache_ratelimit_value == 42

    def test_global_entity_cached_ratelimit_returns_cached_value(self) -> None:
        """Test that global entity _cached_ratelimit returns cached value within interval."""
        entity = self._create_global_entity()
        entity._cache_ratelimit_value = 50
        entity._cache_ratelimit_stamp = 100.0
        entity._cache_ratelimit_interval = 60

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=130.0,  # 30 seconds elapsed, interval is 60
        ):
            result = entity._cached_ratelimit(42)

        assert result == 50  # Cached value

    def test_global_entity_cached_ratelimit_updates_after_interval(self) -> None:
        """Test that global entity _cached_ratelimit updates after interval."""
        entity = self._create_global_entity()
        entity._cache_ratelimit_value = 50
        entity._cache_ratelimit_stamp = 100.0
        entity._cache_ratelimit_interval = 60

        with patch(
            "custom_components.bermuda.entity.monotonic_time_coarse",
            return_value=170.0,  # 70 seconds elapsed, interval is 60
        ):
            result = entity._cached_ratelimit(42)

        assert result == 42  # New value

    def test_global_entity_cached_ratelimit_uses_custom_interval(self) -> None:
        """Test that global entity _cached_ratelimit uses custom interval."""
        entity = self._create_global_entity()

        entity._cached_ratelimit(42, interval=30)

        assert entity._cache_ratelimit_interval == 30


class TestEntityIntegration:
    """Integration tests for entity module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import entity

        assert hasattr(entity, "BermudaEntity")
        assert hasattr(entity, "BermudaGlobalEntity")

    def test_bermuda_entity_inherits_from_coordinator_entity(self) -> None:
        """Test that BermudaEntity inherits from CoordinatorEntity."""
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        assert issubclass(BermudaEntity, CoordinatorEntity)

    def test_bermuda_global_entity_inherits_from_coordinator_entity(self) -> None:
        """Test that BermudaGlobalEntity inherits from CoordinatorEntity."""
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        assert issubclass(BermudaGlobalEntity, CoordinatorEntity)

    def test_scanner_entity_flag_defaults_to_false(self) -> None:
        """Test that _scanner_entity defaults to False on BermudaEntity."""
        entity = object.__new__(BermudaEntity)
        assert entity._scanner_entity is False


class TestDualRoleDeviceInfo:
    """Tests for dual-role devices (both scanner AND tracked).

    Dual-role devices have is_scanner=True but their tracking entities
    (distance, area, device_tracker) should NOT be congealed onto the
    scanner's native device (ESPHome/Shelly). Only scanner-specific
    entities (_scanner_entity=True) should be congealed.
    """

    def _create_entity(
        self,
        address: str = "48:27:e2:e3:f2:d8",
        scanner_entity: bool = False,
        address_wifi_mac: str | None = "48:27:e2:e3:f2:da",
        address_ble_mac: str | None = None,
        entry_id: str | None = "some_entry_id",
    ) -> BermudaEntity:
        """Create a dual-role BermudaEntity (is_scanner=True)."""
        mock_device = MagicMock()
        mock_device.name = "Dual Role Scanner"
        mock_device.unique_id = "dual_role_unique_id"
        mock_device.address = address
        mock_device.address_type = None
        mock_device.is_scanner = True
        mock_device.address_wifi_mac = address_wifi_mac
        mock_device.address_ble_mac = address_ble_mac
        mock_device.fmdn_device_id = None
        mock_device.fmdn_canonical_id = None
        mock_device.entry_id = entry_id

        mock_dr = MagicMock()
        mock_dr.async_get.return_value = None
        mock_dr.async_get_device.return_value = None

        # ESPHome device exists for congealment
        mock_esphome_entry = MagicMock()
        mock_esphome_entry.identifiers = {("esphome", "atoms3-bt-5")}
        mock_esphome_entry.connections = frozenset({("mac", "48:27:e2:e3:f2:da")})
        mock_dr.async_get_device.return_value = mock_esphome_entry

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device
        entity.dr = mock_dr
        entity._scanner_entity = scanner_entity

        return entity

    def test_tracking_entity_uses_bermuda_device_not_esphome(self) -> None:
        """Test that tracking entities (scanner_entity=False) use Bermuda device, not ESPHome.

        This is the core fix: for dual-role devices, tracking entities like
        distance, area, device_tracker must NOT be congealed onto the ESPHome
        device. They should use the regular Bermuda device path with Bermuda
        domain identifiers.
        """
        entity = self._create_entity(scanner_entity=False)

        device_info = entity.device_info

        assert device_info is not None
        # Must use Bermuda domain identifier, NOT ESPHome
        assert (DOMAIN, "dual_role_unique_id") in device_info["identifiers"]
        assert ("esphome", "atoms3-bt-5") not in device_info.get("identifiers", set())

    def test_scanner_entity_congeals_to_esphome(self) -> None:
        """Test that scanner-specific entities (scanner_entity=True) congeal to ESPHome."""
        entity = self._create_entity(scanner_entity=True)

        device_info = entity.device_info

        assert device_info is not None
        # Must use ESPHome identifiers for congealment
        assert ("esphome", "atoms3-bt-5") in device_info["identifiers"]
        assert (DOMAIN, "dual_role_unique_id") not in device_info.get("identifiers", set())

    def test_same_device_different_entity_types_different_targets(self) -> None:
        """Test that the same device produces different device_info based on _scanner_entity.

        This verifies that for the SAME physical device (same address, same is_scanner=True),
        scanner entities go to the ESPHome device and tracking entities stay on Bermuda device.
        """
        scanner_ent = self._create_entity(scanner_entity=True)
        tracking_ent = self._create_entity(scanner_entity=False)

        scanner_info = scanner_ent.device_info
        tracking_info = tracking_ent.device_info

        assert scanner_info is not None
        assert tracking_info is not None

        # Scanner entity targets ESPHome device
        assert ("esphome", "atoms3-bt-5") in scanner_info["identifiers"]

        # Tracking entity targets Bermuda device
        assert (DOMAIN, "dual_role_unique_id") in tracking_info["identifiers"]

        # They MUST point to different device entries
        assert scanner_info["identifiers"] != tracking_info["identifiers"]

    def test_tracking_entity_has_bluetooth_connection(self) -> None:
        """Test that tracking entities for scanner devices still have BLE connection."""
        entity = self._create_entity(scanner_entity=False)

        device_info = entity.device_info

        assert device_info is not None
        bluetooth_conns = [(ct, m) for ct, m in device_info["connections"] if ct == "bluetooth"]
        assert len(bluetooth_conns) == 1


class TestCleanupEmptyBermudaDevices:
    """Tests for async_cleanup_empty_bermuda_devices coordinator method."""

    def _make_coordinator(self) -> MagicMock:
        """Create a mock coordinator with device and entity registries."""
        coordinator = MagicMock()
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry_id"
        coordinator.dr = MagicMock()
        coordinator.er = MagicMock()
        return coordinator

    def _make_device_entry(
        self,
        device_id: str,
        identifiers: set,
        name: str = "Test Device",
    ) -> MagicMock:
        """Create a mock device registry entry."""
        entry = MagicMock()
        entry.id = device_id
        entry.name = name
        entry.identifiers = identifiers
        return entry

    @pytest.mark.asyncio
    async def test_removes_empty_bermuda_device(self) -> None:
        """Test that empty Bermuda devices are removed."""
        from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

        coordinator = self._make_coordinator()
        empty_device = self._make_device_entry("dev1", {(DOMAIN, "aa:bb:cc:dd:ee:ff")})
        coordinator.dr.devices.values.return_value = [empty_device]

        # Device has zero entities
        with patch(
            "custom_components.bermuda.coordinator.er.async_entries_for_device",
            return_value=[],
        ):
            await BermudaDataUpdateCoordinator.async_cleanup_empty_bermuda_devices(coordinator)

        coordinator.dr.async_remove_device.assert_called_once_with("dev1")

    @pytest.mark.asyncio
    async def test_keeps_device_with_entities(self) -> None:
        """Test that Bermuda devices with entities are kept."""
        from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

        coordinator = self._make_coordinator()
        device_with_entities = self._make_device_entry("dev1", {(DOMAIN, "aa:bb:cc:dd:ee:ff")})
        coordinator.dr.devices.values.return_value = [device_with_entities]

        # Device has one entity
        mock_entity = MagicMock()
        with patch(
            "custom_components.bermuda.coordinator.er.async_entries_for_device",
            return_value=[mock_entity],
        ):
            await BermudaDataUpdateCoordinator.async_cleanup_empty_bermuda_devices(coordinator)

        coordinator.dr.async_remove_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_bermuda_devices(self) -> None:
        """Test that non-Bermuda devices are not touched."""
        from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

        coordinator = self._make_coordinator()
        esphome_device = self._make_device_entry("dev1", {("esphome", "my-esp")})
        coordinator.dr.devices.values.return_value = [esphome_device]

        with patch(
            "custom_components.bermuda.coordinator.er.async_entries_for_device",
            return_value=[],
        ):
            await BermudaDataUpdateCoordinator.async_cleanup_empty_bermuda_devices(coordinator)

        coordinator.dr.async_remove_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_global_bermuda_device(self) -> None:
        """Test that the BERMUDA_GLOBAL device is never removed."""
        from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

        coordinator = self._make_coordinator()
        global_device = self._make_device_entry("dev1", {(DOMAIN, "BERMUDA_GLOBAL")})
        coordinator.dr.devices.values.return_value = [global_device]

        with patch(
            "custom_components.bermuda.coordinator.er.async_entries_for_device",
            return_value=[],
        ):
            await BermudaDataUpdateCoordinator.async_cleanup_empty_bermuda_devices(coordinator)

        coordinator.dr.async_remove_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_config_entry(self) -> None:
        """Test that cleanup does nothing when config_entry is None."""
        from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

        coordinator = self._make_coordinator()
        coordinator.config_entry = None

        await BermudaDataUpdateCoordinator.async_cleanup_empty_bermuda_devices(coordinator)

        coordinator.dr.async_remove_device.assert_not_called()
