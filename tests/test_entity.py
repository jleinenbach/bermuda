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
        unique_id: str = "test_unique_id",
        fmdn_device_id: str | None = None,
        fmdn_canonical_id: str | None = None,
    ) -> BermudaEntity:
        """Create a BermudaEntity instance for testing."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = unique_id
        mock_device.address = address
        mock_device.address_type = address_type
        mock_device.is_scanner = is_scanner
        mock_device.address_wifi_mac = None
        mock_device.address_ble_mac = None
        mock_device.fmdn_device_id = fmdn_device_id
        mock_device.fmdn_canonical_id = fmdn_canonical_id

        mock_dr = MagicMock()
        mock_dr.async_get.return_value = None

        entity = object.__new__(BermudaEntity)
        entity._device = mock_device
        entity.dr = mock_dr

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
        """Test device_info for scanner device."""
        entity = self._create_entity(
            address="aa:bb:cc:dd:ee:ff",
            is_scanner=True,
        )

        device_info = entity.device_info

        assert device_info is not None
        # Scanner should have bluetooth connection
        assert len(device_info["connections"]) > 0


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
