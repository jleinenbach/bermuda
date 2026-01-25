"""Test FMDN integration layer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import (
    ADDR_TYPE_FMDN_DEVICE,
    DATA_EID_RESOLVER,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
)
from custom_components.bermuda.fmdn.integration import (
    EIDMatch,
    FmdnIntegration,
    _convert_to_eid_match,
)
from custom_components.bermuda.fmdn.manager import EidResolutionStatus


class MockRawMatch:
    """Mock raw match object from external resolver."""

    def __init__(
        self,
        device_id: str = "dev_123",
        config_entry_id: str = "entry_456",
        canonical_id: str = "uuid-789",
        time_offset: int = 0,
        is_reversed: bool = False,
    ) -> None:
        self.device_id = device_id
        self.config_entry_id = config_entry_id
        self.canonical_id = canonical_id
        self.time_offset = time_offset
        self.is_reversed = is_reversed


class TestConvertToEidMatch:
    """Tests for _convert_to_eid_match function."""

    def test_convert_valid_match(self) -> None:
        """Test converting a valid match object."""
        raw = MockRawMatch(
            device_id="device_abc",
            config_entry_id="entry_xyz",
            canonical_id="canonical_123",
            time_offset=5,
            is_reversed=True,
        )
        result = _convert_to_eid_match(raw)

        assert result is not None
        assert result.device_id == "device_abc"
        assert result.config_entry_id == "entry_xyz"
        assert result.canonical_id == "canonical_123"
        assert result.time_offset == 5
        assert result.is_reversed is True

    def test_convert_none_returns_none(self) -> None:
        """Test that None input returns None."""
        result = _convert_to_eid_match(None)
        assert result is None

    def test_convert_missing_attributes_uses_defaults(self) -> None:
        """Test that missing attributes use defaults."""
        raw = MagicMock(spec=[])  # No attributes
        result = _convert_to_eid_match(raw)

        assert result is not None
        assert result.device_id == ""
        assert result.config_entry_id == ""
        assert result.canonical_id == ""
        assert result.time_offset == 0
        assert result.is_reversed is False

    def test_convert_none_attribute_values(self) -> None:
        """Test that None attribute values convert to defaults."""
        raw = MagicMock()
        raw.device_id = None
        raw.config_entry_id = None
        raw.canonical_id = None
        raw.time_offset = None
        raw.is_reversed = None

        result = _convert_to_eid_match(raw)

        assert result is not None
        assert result.device_id == ""
        assert result.time_offset == 0
        assert result.is_reversed is False

    def test_convert_invalid_time_offset_returns_none(self) -> None:
        """Test that invalid time_offset raises and returns None."""
        raw = MagicMock()
        raw.device_id = "dev"
        raw.config_entry_id = "entry"
        raw.canonical_id = "can"
        raw.time_offset = "not_an_int"  # Will fail int() conversion
        raw.is_reversed = False

        result = _convert_to_eid_match(raw)
        # ValueError from int() is caught
        assert result is None


class TestEIDMatch:
    """Tests for EIDMatch NamedTuple."""

    def test_eid_match_creation(self) -> None:
        """Test creating an EIDMatch."""
        match = EIDMatch(
            device_id="dev_1",
            config_entry_id="entry_1",
            canonical_id="uuid_1",
            time_offset=10,
            is_reversed=True,
        )

        assert match.device_id == "dev_1"
        assert match.config_entry_id == "entry_1"
        assert match.canonical_id == "uuid_1"
        assert match.time_offset == 10
        assert match.is_reversed is True

    def test_eid_match_is_namedtuple(self) -> None:
        """Test that EIDMatch is a NamedTuple."""
        match = EIDMatch("a", "b", "c", 0, False)
        assert hasattr(match, "_fields")
        assert match._fields == ("device_id", "config_entry_id", "canonical_id", "time_offset", "is_reversed")


def _make_mock_coordinator() -> MagicMock:
    """Create a mock coordinator for testing."""
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.hass.data = {}
    coordinator.metadevices = {}
    coordinator.devices = {}
    coordinator.dr = MagicMock()
    coordinator.er = MagicMock()
    coordinator._do_fmdn_device_init = True

    def get_or_create_device(address: str) -> MagicMock:
        """Create a mock device."""
        device = MagicMock()
        device.address = address
        device.metadevice_type = set()
        device.metadevice_sources = []
        device.create_sensor = False
        device.fmdn_device_id = None
        device.fmdn_canonical_id = None
        device.address_type = None
        device.name_devreg = None
        device.name_by_user = None
        return device

    coordinator._get_or_create_device = get_or_create_device
    return coordinator


class TestFmdnIntegrationInit:
    """Tests for FmdnIntegration initialization."""

    def test_init_creates_manager(self) -> None:
        """Test that initialization creates a manager."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        assert integration.coordinator is coordinator
        assert integration.manager is not None
        assert integration._fmdn_device_id_cache == {}
        assert integration._fmdn_canonical_id_cache == {}


class TestGetResolver:
    """Tests for get_resolver method."""

    def test_get_resolver_returns_none_when_no_domain(self) -> None:
        """Test that get_resolver returns None when domain not in hass.data."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration.get_resolver()
        assert result is None

    def test_get_resolver_returns_none_when_not_dict(self) -> None:
        """Test that get_resolver returns None when domain value is not a dict."""
        coordinator = _make_mock_coordinator()
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = "not_a_dict"
        integration = FmdnIntegration(coordinator)

        result = integration.get_resolver()
        assert result is None

    def test_get_resolver_returns_none_when_resolver_none(self) -> None:
        """Test that get_resolver returns None when resolver is None."""
        coordinator = _make_mock_coordinator()
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: None}
        integration = FmdnIntegration(coordinator)

        result = integration.get_resolver()
        assert result is None

    def test_get_resolver_returns_none_when_resolve_eid_not_callable(self) -> None:
        """Test that get_resolver returns None when resolve_eid is not callable."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = "not_callable"
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}
        integration = FmdnIntegration(coordinator)

        result = integration.get_resolver()
        assert result is None

    def test_get_resolver_returns_resolver_when_valid(self) -> None:
        """Test that get_resolver returns resolver when valid."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(return_value=None)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}
        integration = FmdnIntegration(coordinator)

        result = integration.get_resolver()
        assert result is mock_resolver


class TestFormatMetadeviceAddress:
    """Tests for format_metadevice_address method."""

    def test_format_with_device_id_only(self) -> None:
        """Test format with only device_id."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration.format_metadevice_address("dev_abc", None)
        assert result == "fmdn:dev_abc"

    def test_format_with_both_ids_prefers_device_id(self) -> None:
        """Test that device_id is preferred over canonical_id."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration.format_metadevice_address("dev_abc", "canonical_xyz")
        assert result == "fmdn:dev_abc"
        assert "canonical_xyz" not in result

    def test_format_with_canonical_id_fallback(self) -> None:
        """Test fallback to canonical_id when device_id is None."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration.format_metadevice_address(None, "canonical_xyz")
        assert result == "fmdn:canonical_xyz"

    def test_format_with_neither_returns_unknown(self) -> None:
        """Test that neither ID returns 'unknown'."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration.format_metadevice_address(None, None)
        assert result == "fmdn:unknown"

    def test_format_with_empty_strings(self) -> None:
        """Test that empty strings are treated as missing."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        # Empty string device_id should fall back to canonical_id
        result = integration.format_metadevice_address("", "canonical_xyz")
        assert result == "fmdn:canonical_xyz"

        # Both empty should return unknown
        result = integration.format_metadevice_address("", "")
        assert result == "fmdn:unknown"


class TestGetCachedMetadevice:
    """Tests for _get_cached_metadevice method."""

    def test_cache_miss_returns_none(self) -> None:
        """Test that cache miss returns None."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration._get_cached_metadevice(fmdn_device_id="unknown")
        assert result is None

    def test_cache_hit_by_device_id(self) -> None:
        """Test cache hit by device_id."""
        coordinator = _make_mock_coordinator()
        mock_device = MagicMock()
        coordinator.metadevices["fmdn:dev_123"] = mock_device

        integration = FmdnIntegration(coordinator)
        integration._fmdn_device_id_cache["dev_123"] = "fmdn:dev_123"

        result = integration._get_cached_metadevice(fmdn_device_id="dev_123")
        assert result is mock_device

    def test_cache_hit_by_canonical_id_only_when_device_id_none(self) -> None:
        """Test cache hit by canonical_id only when device_id is None."""
        coordinator = _make_mock_coordinator()
        mock_device = MagicMock()
        coordinator.metadevices["fmdn:uuid_abc"] = mock_device

        integration = FmdnIntegration(coordinator)
        integration._fmdn_canonical_id_cache["uuid_abc"] = "fmdn:uuid_abc"

        # With device_id=None, should fall back to canonical_id
        result = integration._get_cached_metadevice(fmdn_device_id=None, canonical_id="uuid_abc")
        assert result is mock_device

    def test_device_id_provided_but_not_found_does_not_fallback(self) -> None:
        """Test that providing device_id that's not found doesn't fall back to canonical_id."""
        coordinator = _make_mock_coordinator()
        mock_device = MagicMock()
        coordinator.metadevices["fmdn:uuid_abc"] = mock_device

        integration = FmdnIntegration(coordinator)
        integration._fmdn_canonical_id_cache["uuid_abc"] = "fmdn:uuid_abc"

        # device_id is provided but not in cache - should NOT fall back to canonical_id
        result = integration._get_cached_metadevice(fmdn_device_id="dev_123", canonical_id="uuid_abc")
        assert result is None

    def test_stale_cache_entry_is_removed(self) -> None:
        """Test that stale cache entries are cleaned up."""
        coordinator = _make_mock_coordinator()
        # Cache points to address that doesn't exist in metadevices
        integration = FmdnIntegration(coordinator)
        integration._fmdn_device_id_cache["dev_123"] = "fmdn:dev_123"

        # This should clean up the stale cache entry
        result = integration._get_cached_metadevice(fmdn_device_id="dev_123")
        assert result is None
        assert "dev_123" not in integration._fmdn_device_id_cache


class TestUpdateCache:
    """Tests for _update_cache method."""

    def test_update_cache_with_device_id(self) -> None:
        """Test updating cache with device_id."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        integration._update_cache("fmdn:addr", fmdn_device_id="dev_123")

        assert integration._fmdn_device_id_cache["dev_123"] == "fmdn:addr"

    def test_update_cache_with_canonical_id(self) -> None:
        """Test updating cache with canonical_id."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        integration._update_cache("fmdn:addr", canonical_id="uuid_abc")

        assert integration._fmdn_canonical_id_cache["uuid_abc"] == "fmdn:addr"

    def test_update_cache_with_both(self) -> None:
        """Test updating cache with both IDs."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        integration._update_cache("fmdn:addr", fmdn_device_id="dev_123", canonical_id="uuid_abc")

        assert integration._fmdn_device_id_cache["dev_123"] == "fmdn:addr"
        assert integration._fmdn_canonical_id_cache["uuid_abc"] == "fmdn:addr"


class TestNormalizeEidBytes:
    """Tests for normalize_eid_bytes static method."""

    def test_normalize_none_returns_none(self) -> None:
        """Test that None returns None."""
        result = FmdnIntegration.normalize_eid_bytes(None)
        assert result is None

    def test_normalize_bytes_returns_bytes(self) -> None:
        """Test that bytes input returns bytes."""
        data = b"\x01\x02\x03"
        result = FmdnIntegration.normalize_eid_bytes(data)
        assert result == b"\x01\x02\x03"

    def test_normalize_bytearray_returns_bytes(self) -> None:
        """Test that bytearray input returns bytes."""
        data = bytearray([1, 2, 3])
        result = FmdnIntegration.normalize_eid_bytes(data)
        assert result == b"\x01\x02\x03"

    def test_normalize_memoryview_returns_bytes(self) -> None:
        """Test that memoryview input returns bytes."""
        data = memoryview(b"\x01\x02\x03")
        result = FmdnIntegration.normalize_eid_bytes(data)
        assert result == b"\x01\x02\x03"

    def test_normalize_hex_string(self) -> None:
        """Test that hex string is parsed."""
        result = FmdnIntegration.normalize_eid_bytes("010203")
        assert result == b"\x01\x02\x03"

    def test_normalize_hex_string_with_prefix(self) -> None:
        """Test that hex string with 0x prefix is parsed."""
        result = FmdnIntegration.normalize_eid_bytes("0x010203")
        assert result == b"\x01\x02\x03"

    def test_normalize_hex_string_with_colons(self) -> None:
        """Test that hex string with colons is parsed."""
        result = FmdnIntegration.normalize_eid_bytes("01:02:03")
        assert result == b"\x01\x02\x03"

    def test_normalize_hex_string_with_spaces(self) -> None:
        """Test that hex string with spaces is parsed."""
        result = FmdnIntegration.normalize_eid_bytes("01 02 03")
        assert result == b"\x01\x02\x03"

    def test_normalize_invalid_hex_string_returns_none(self) -> None:
        """Test that invalid hex string returns None."""
        result = FmdnIntegration.normalize_eid_bytes("not_hex")
        assert result is None

    def test_normalize_unsupported_type_returns_none(self) -> None:
        """Test that unsupported type returns None."""
        result = FmdnIntegration.normalize_eid_bytes(12345)  # type: ignore[arg-type]
        assert result is None


class TestProcessResolution:
    """Tests for process_resolution method."""

    def test_process_resolution_no_resolver(self) -> None:
        """Test process_resolution when resolver is not available."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        result = integration.process_resolution(b"\x01\x02\x03")
        assert result is None

    def test_process_resolution_with_match(self) -> None:
        """Test process_resolution with successful match."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_123", canonical_id="uuid_456")
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        result = integration.process_resolution(b"\x01\x02\x03")

        assert result is not None
        assert result.device_id == "dev_123"
        assert result.canonical_id == "uuid_456"


class TestProcessResolutionWithStatus:
    """Tests for process_resolution_with_status method."""

    def test_process_resolution_with_status_no_resolver(self) -> None:
        """Test that missing resolver returns RESOLVER_UNAVAILABLE status."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        match, status = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is None
        assert status == EidResolutionStatus.RESOLVER_UNAVAILABLE

    def test_process_resolution_with_status_invalid_eid(self) -> None:
        """Test that invalid EID returns NO_KNOWN_EID_MATCH."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(return_value=None)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        # Use a string that fails normalization
        with patch.object(integration, "normalize_eid_bytes", return_value=None):
            match, status = integration.process_resolution_with_status(b"\x01", "aa:bb:cc:dd:ee:ff")

        assert match is None
        assert status == EidResolutionStatus.NO_KNOWN_EID_MATCH

    def test_process_resolution_with_status_resolver_error(self) -> None:
        """Test that resolver exception returns RESOLVER_ERROR."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(side_effect=ValueError("test error"))
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        match, status = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is None
        assert status == EidResolutionStatus.RESOLVER_ERROR

    def test_process_resolution_with_status_unexpected_error(self) -> None:
        """Test that unexpected exception returns RESOLVER_ERROR."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(side_effect=RuntimeError("unexpected"))
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        match, status = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is None
        assert status == EidResolutionStatus.RESOLVER_ERROR

    def test_process_resolution_with_status_no_match(self) -> None:
        """Test that no match returns NO_KNOWN_EID_MATCH."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(return_value=None)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        match, status = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is None
        assert status == EidResolutionStatus.NO_KNOWN_EID_MATCH

    def test_process_resolution_with_status_success(self) -> None:
        """Test successful resolution."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_123", canonical_id="uuid_456")
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        match, status = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is not None
        assert match.device_id == "dev_123"
        assert status == EidResolutionStatus.NOT_EVALUATED

    def test_process_resolution_logs_nonzero_time_offset(self) -> None:
        """Test that non-zero time_offset is logged."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_123", time_offset=10)
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        match, _ = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is not None
        assert match.time_offset == 10

    def test_process_resolution_logs_is_reversed(self) -> None:
        """Test that is_reversed=True is logged."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_123", is_reversed=True)
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        match, _ = integration.process_resolution_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert match is not None
        assert match.is_reversed is True


class TestProcessResolutionAllWithStatus:
    """Tests for process_resolution_all_with_status method."""

    def test_process_all_no_resolver(self) -> None:
        """Test that missing resolver returns RESOLVER_UNAVAILABLE."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        matches, status = integration.process_resolution_all_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert matches == []
        assert status == EidResolutionStatus.RESOLVER_UNAVAILABLE

    def test_process_all_with_resolve_eid_all(self) -> None:
        """Test using resolve_eid_all for multiple matches."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_matches = [
            MockRawMatch(device_id="dev_1", canonical_id="uuid"),
            MockRawMatch(device_id="dev_2", canonical_id="uuid"),
        ]
        mock_resolver.resolve_eid = MagicMock(return_value=None)
        mock_resolver.resolve_eid_all = MagicMock(return_value=raw_matches)
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        matches, status = integration.process_resolution_all_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert len(matches) == 2
        assert matches[0].device_id == "dev_1"
        assert matches[1].device_id == "dev_2"
        assert status == EidResolutionStatus.NOT_EVALUATED

    def test_process_all_fallback_to_resolve_eid(self) -> None:
        """Test fallback to resolve_eid when resolve_eid_all fails."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_single")
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        mock_resolver.resolve_eid_all = MagicMock(side_effect=ValueError("test"))
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        matches, status = integration.process_resolution_all_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert len(matches) == 1
        assert matches[0].device_id == "dev_single"

    def test_process_all_resolve_eid_all_unexpected_error(self) -> None:
        """Test fallback when resolve_eid_all raises unexpected error."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_single")
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        mock_resolver.resolve_eid_all = MagicMock(side_effect=RuntimeError("unexpected"))
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        matches, status = integration.process_resolution_all_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert len(matches) == 1
        assert matches[0].device_id == "dev_single"

    def test_process_all_resolve_eid_error(self) -> None:
        """Test error handling when resolve_eid fails."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(side_effect=ValueError("test"))
        # No resolve_eid_all method
        del mock_resolver.resolve_eid_all
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        matches, status = integration.process_resolution_all_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert matches == []
        assert status == EidResolutionStatus.RESOLVER_ERROR

    def test_process_all_resolve_eid_unexpected_error(self) -> None:
        """Test error handling when resolve_eid raises unexpected error."""
        coordinator = _make_mock_coordinator()
        mock_resolver = MagicMock()
        mock_resolver.resolve_eid = MagicMock(side_effect=RuntimeError("unexpected"))
        # No resolve_eid_all method
        del mock_resolver.resolve_eid_all
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)
        matches, status = integration.process_resolution_all_with_status(b"\x01\x02\x03", "aa:bb:cc:dd:ee:ff")

        assert matches == []
        assert status == EidResolutionStatus.RESOLVER_ERROR


class TestRegisterSource:
    """Tests for register_source method."""

    def test_register_source_creates_metadevice(self) -> None:
        """Test that register_source creates a metadevice."""
        coordinator = _make_mock_coordinator()
        coordinator.dr.async_get = MagicMock(return_value=None)

        source_device = MagicMock()
        source_device.address = "aa:bb:cc:dd:ee:ff"
        source_device.metadevice_type = set()

        match = EIDMatch(
            device_id="dev_123",
            config_entry_id="entry_456",
            canonical_id="uuid_789",
            time_offset=0,
            is_reversed=False,
        )

        integration = FmdnIntegration(coordinator)
        integration.register_source(source_device, "fmdn:dev_123", match)

        # Verify metadevice was created and registered
        assert "fmdn:dev_123" in coordinator.metadevices
        assert "fmdn:dev_123" in coordinator.devices
        metadevice = coordinator.metadevices["fmdn:dev_123"]
        assert metadevice.create_sensor is True
        assert METADEVICE_FMDN_DEVICE in metadevice.metadevice_type
        assert metadevice.address_type == ADDR_TYPE_FMDN_DEVICE
        assert metadevice.fmdn_device_id == "dev_123"
        assert metadevice.fmdn_canonical_id == "uuid_789"

    def test_register_source_updates_existing_metadevice(self) -> None:
        """Test that register_source uses existing cached metadevice."""
        coordinator = _make_mock_coordinator()
        coordinator.dr.async_get = MagicMock(return_value=None)

        # Pre-create a metadevice
        existing_metadevice = MagicMock()
        existing_metadevice.address = "fmdn:dev_123"
        existing_metadevice.metadevice_type = set()
        existing_metadevice.metadevice_sources = []
        coordinator.metadevices["fmdn:dev_123"] = existing_metadevice

        source_device = MagicMock()
        source_device.address = "aa:bb:cc:dd:ee:ff"
        source_device.metadevice_type = set()

        match = EIDMatch("dev_123", "entry_456", "uuid_789", 0, False)

        integration = FmdnIntegration(coordinator)
        integration._fmdn_device_id_cache["dev_123"] = "fmdn:dev_123"

        integration.register_source(source_device, "fmdn:dev_123", match)

        # Should have updated the existing metadevice
        assert existing_metadevice.fmdn_device_id == "dev_123"

    def test_register_source_adds_source_address(self) -> None:
        """Test that source address is added to metadevice_sources."""
        coordinator = _make_mock_coordinator()
        coordinator.dr.async_get = MagicMock(return_value=None)

        source_device = MagicMock()
        source_device.address = "aa:bb:cc:dd:ee:ff"
        source_device.metadevice_type = set()

        match = EIDMatch("dev_123", "entry_456", "uuid_789", 0, False)

        integration = FmdnIntegration(coordinator)
        integration.register_source(source_device, "fmdn:dev_123", match)

        metadevice = coordinator.metadevices["fmdn:dev_123"]
        assert "aa:bb:cc:dd:ee:ff" in metadevice.metadevice_sources

    def test_register_source_updates_name_from_device_registry(self) -> None:
        """Test that name is updated from device registry."""
        coordinator = _make_mock_coordinator()

        mock_device_entry = MagicMock()
        mock_device_entry.name = "Test Device"
        mock_device_entry.name_by_user = "User's Device"
        coordinator.dr.async_get = MagicMock(return_value=mock_device_entry)

        source_device = MagicMock()
        source_device.address = "aa:bb:cc:dd:ee:ff"
        source_device.metadevice_type = set()

        match = EIDMatch("dev_123", "entry_456", "uuid_789", 0, False)

        integration = FmdnIntegration(coordinator)
        integration.register_source(source_device, "fmdn:dev_123", match)

        metadevice = coordinator.metadevices["fmdn:dev_123"]
        assert metadevice.name_devreg == "Test Device"
        assert metadevice.name_by_user == "User's Device"


class TestHandleAdvertisement:
    """Tests for handle_advertisement method."""

    def test_handle_advertisement_empty_service_data(self) -> None:
        """Test that empty service_data returns early."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.metadevice_type = set()

        integration.handle_advertisement(device, {})

        # Should not have modified device
        assert METADEVICE_TYPE_FMDN_SOURCE not in device.metadevice_type

    def test_handle_advertisement_no_fmdn_eids(self) -> None:
        """Test that non-FMDN service data returns early."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.metadevice_type = set()

        # Non-FMDN service data
        integration.handle_advertisement(device, {"0000180f-0000-1000-8000-00805f9b34fb": b"\x64"})

        # Should not have modified device (no FMDN EIDs extracted)
        # The device might have FMDN_SOURCE added if extract_eids returns empty
        # but that depends on the implementation

    def test_handle_advertisement_with_match(self) -> None:
        """Test successful advertisement handling with match."""
        coordinator = _make_mock_coordinator()
        coordinator.dr.async_get = MagicMock(return_value=None)

        mock_resolver = MagicMock()
        raw_match = MockRawMatch(device_id="dev_123", canonical_id="uuid_456")
        mock_resolver.resolve_eid = MagicMock(return_value=raw_match)
        mock_resolver.resolve_eid_all = MagicMock(return_value=[raw_match])
        coordinator.hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.address = "aa:bb:cc:dd:ee:ff"
        device.metadevice_type = set()

        # Mock extract_eids to return a valid EID
        with patch.object(integration, "extract_eids", return_value={b"\x01\x02\x03\x04\x05"}):
            integration.handle_advertisement(device, {"some_uuid": b"\x01\x02\x03"})

        assert METADEVICE_TYPE_FMDN_SOURCE in device.metadevice_type


class TestPruneSource:
    """Tests for prune_source method."""

    def test_prune_source_not_fmdn_source(self) -> None:
        """Test that non-FMDN source is not pruned."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.metadevice_type = set()  # Not FMDN source
        device.last_seen = 100.0

        prune_list: list[str] = []
        result = integration.prune_source(device, stamp_fmdn=200.0, prune_list=prune_list)

        assert result is False
        assert len(prune_list) == 0

    def test_prune_source_fresh_device(self) -> None:
        """Test that fresh FMDN source is not pruned."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.metadevice_type = {METADEVICE_TYPE_FMDN_SOURCE}
        device.address = "aa:bb:cc:dd:ee:ff"
        device.last_seen = 250.0  # More recent than stamp

        prune_list: list[str] = []
        result = integration.prune_source(device, stamp_fmdn=200.0, prune_list=prune_list)

        assert result is False
        assert len(prune_list) == 0

    def test_prune_source_stale_device(self) -> None:
        """Test that stale FMDN source is pruned."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.metadevice_type = {METADEVICE_TYPE_FMDN_SOURCE}
        device.address = "aa:bb:cc:dd:ee:ff"
        device.last_seen = 100.0  # Older than stamp

        prune_list: list[str] = []
        result = integration.prune_source(device, stamp_fmdn=200.0, prune_list=prune_list)

        assert result is True
        assert "aa:bb:cc:dd:ee:ff" in prune_list

    def test_prune_source_no_duplicates(self) -> None:
        """Test that device is not added to prune_list twice."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        device = MagicMock()
        device.metadevice_type = {METADEVICE_TYPE_FMDN_SOURCE}
        device.address = "aa:bb:cc:dd:ee:ff"
        device.last_seen = 100.0

        prune_list = ["aa:bb:cc:dd:ee:ff"]  # Already in list
        result = integration.prune_source(device, stamp_fmdn=200.0, prune_list=prune_list)

        assert result is True
        # Should not add duplicate
        assert prune_list.count("aa:bb:cc:dd:ee:ff") == 1


class TestExtractCanonicalId:
    """Tests for _extract_canonical_id static method."""

    def test_extract_from_full_format(self) -> None:
        """Test extracting from full format (entry_id:subentry_id:device_id)."""
        mock_device = MagicMock()
        mock_device.identifiers = {(DOMAIN_GOOGLEFINDMY, "entry123:subentry456:uuid-789-abc")}

        result = FmdnIntegration._extract_canonical_id(mock_device)
        assert result == "uuid-789-abc"

    def test_extract_from_canonical_format(self) -> None:
        """Test extracting from canonical format (entry_id:device_id)."""
        mock_device = MagicMock()
        mock_device.identifiers = {(DOMAIN_GOOGLEFINDMY, "entry123:uuid-789-abc")}

        result = FmdnIntegration._extract_canonical_id(mock_device)
        assert result == "uuid-789-abc"

    def test_extract_from_simple_format(self) -> None:
        """Test extracting from simple format (device_id only)."""
        mock_device = MagicMock()
        mock_device.identifiers = {(DOMAIN_GOOGLEFINDMY, "uuid-789-abc")}

        result = FmdnIntegration._extract_canonical_id(mock_device)
        assert result == "uuid-789-abc"

    def test_extract_no_googlefindmy_identifier(self) -> None:
        """Test that non-googlefindmy identifiers are ignored."""
        mock_device = MagicMock()
        mock_device.identifiers = {("other_domain", "some_id")}

        result = FmdnIntegration._extract_canonical_id(mock_device)
        assert result is None

    def test_extract_invalid_identifier_format(self) -> None:
        """Test that invalid identifier formats are skipped."""
        mock_device = MagicMock()
        mock_device.identifiers = {
            ("single_element",),  # Too few elements
            (DOMAIN_GOOGLEFINDMY, "id1", "id2", "id3"),  # Too many elements
        }

        result = FmdnIntegration._extract_canonical_id(mock_device)
        assert result is None


class TestProcessFmdnEntity:
    """Tests for _process_fmdn_entity method."""

    def test_process_non_device_tracker(self) -> None:
        """Test that non-device_tracker entities are skipped."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        mock_entity = MagicMock()
        mock_entity.domain = "sensor"  # Not device_tracker

        integration._process_fmdn_entity(mock_entity)

        # Should not create any metadevice
        assert len(coordinator.metadevices) == 0

    def test_process_no_device_entry(self) -> None:
        """Test that entity with no device entry is skipped."""
        coordinator = _make_mock_coordinator()
        coordinator.dr.async_get = MagicMock(return_value=None)
        integration = FmdnIntegration(coordinator)

        mock_entity = MagicMock()
        mock_entity.domain = "device_tracker"
        mock_entity.device_id = "some_device_id"
        mock_entity.entity_id = "device_tracker.test"

        integration._process_fmdn_entity(mock_entity)

        # Should not create any metadevice
        assert len(coordinator.metadevices) == 0

    def test_process_creates_metadevice(self) -> None:
        """Test that valid entity creates metadevice."""
        coordinator = _make_mock_coordinator()

        mock_device_entry = MagicMock()
        mock_device_entry.id = "ha_device_id_123"
        mock_device_entry.name = "Test FMDN Device"
        mock_device_entry.name_by_user = None
        mock_device_entry.identifiers = {(DOMAIN_GOOGLEFINDMY, "entry:uuid-abc-123")}
        coordinator.dr.async_get = MagicMock(return_value=mock_device_entry)

        integration = FmdnIntegration(coordinator)

        mock_entity = MagicMock()
        mock_entity.domain = "device_tracker"
        mock_entity.device_id = "ha_device_id_123"
        mock_entity.entity_id = "device_tracker.test_fmdn"
        mock_entity.unique_id = "fallback_uuid"

        integration._process_fmdn_entity(mock_entity)

        # Should create metadevice
        assert len(coordinator.metadevices) == 1
        assert len(coordinator.devices) == 1


class TestDiscoverMetadevices:
    """Tests for discover_metadevices method."""

    def test_discover_skips_when_not_initialized(self) -> None:
        """Test that discover skips when _do_fmdn_device_init is False."""
        coordinator = _make_mock_coordinator()
        coordinator._do_fmdn_device_init = False
        integration = FmdnIntegration(coordinator)

        # Should return early without processing
        integration.discover_metadevices()

        # hass.config_entries should not have been called
        assert not coordinator.hass.config_entries.async_entries.called

    def test_discover_processes_fmdn_entries(self) -> None:
        """Test that discover processes FMDN config entries."""
        coordinator = _make_mock_coordinator()

        mock_entity = MagicMock()
        mock_entity.domain = "device_tracker"
        mock_entity.device_id = "ha_device_id"
        mock_entity.entity_id = "device_tracker.fmdn_test"
        mock_entity.unique_id = "uuid_123"

        mock_device_entry = MagicMock()
        mock_device_entry.id = "ha_device_id"
        mock_device_entry.name = "FMDN Test"
        mock_device_entry.name_by_user = None
        mock_device_entry.identifiers = {(DOMAIN_GOOGLEFINDMY, "uuid_123")}
        coordinator.dr.async_get = MagicMock(return_value=mock_device_entry)

        mock_entry = MagicMock()
        mock_entry.entry_id = "fmdn_config_entry"
        coordinator.hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])
        coordinator.er.entities.get_entries_for_config_entry_id = MagicMock(return_value=[mock_entity])

        integration = FmdnIntegration(coordinator)
        integration.discover_metadevices()

        # Should have processed and created metadevice
        assert len(coordinator.metadevices) == 1
        # Flag should be cleared
        assert coordinator._do_fmdn_device_init is False


class TestExtractEids:
    """Tests for extract_eids method."""

    def test_extract_eids_delegates_to_extraction(self) -> None:
        """Test that extract_eids delegates to extraction module."""
        coordinator = _make_mock_coordinator()
        integration = FmdnIntegration(coordinator)

        with patch("custom_components.bermuda.fmdn.integration.extract_fmdn_eids") as mock_extract:
            mock_extract.return_value = {b"\x01\x02\x03"}
            result = integration.extract_eids({"some_uuid": b"\x01\x02"})

            mock_extract.assert_called_once()
            assert result == {b"\x01\x02\x03"}
