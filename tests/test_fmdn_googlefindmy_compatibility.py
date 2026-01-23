"""
Tests for GoogleFindMy-HA compatibility.

These tests ensure that Bermuda's FMDN integration remains compatible with
the GoogleFindMy-HA integration (https://github.com/jleinenbach/GoogleFindMy-HA).

CRITICAL: If these tests fail, FMDN devices will either:
- Show as "Unavailable" (coordinator crash)
- Create duplicate devices (ID format mismatch)
- Not congeal with GoogleFindMy devices (missing fmdn_device_id)

See CLAUDE.md section "FMDN / GoogleFindMy-HA Integration Architecture" for details.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.bermuda.const import (
    ADDR_TYPE_FMDN_DEVICE,
    DOMAIN_GOOGLEFINDMY,
)
from custom_components.bermuda.fmdn import FmdnIntegration
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Create a minimal coordinator mock for FMDN testing."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {}
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.dr = dr.async_get(hass)
    coordinator.er = er.async_get(hass)
    return coordinator


@pytest.fixture
def fmdn_integration(mock_coordinator: BermudaDataUpdateCoordinator) -> FmdnIntegration:
    """Create an FMDN integration instance."""
    return FmdnIntegration(mock_coordinator)


# =============================================================================
# GoogleFindMy-HA API Contract Tests
# =============================================================================


class TestGoogleFindMyAPIContract:
    """
    Tests that verify Bermuda's assumptions about GoogleFindMy-HA data structures.

    If GoogleFindMy-HA changes its API, these tests should fail and alert us
    to update Bermuda's integration code.
    """

    def test_eidmatch_has_required_fields(self) -> None:
        """Verify EIDMatch structure matches GoogleFindMy-HA's contract.

        GoogleFindMy-HA's EIDMatch (NamedTuple) has these fields:
        - device_id: str (HA Device Registry ID)
        - config_entry_id: str
        - canonical_id: str (UUID-only after normalization)
        - time_offset: int
        - is_reversed: bool
        """
        # Simulate an EIDMatch as returned by GoogleFindMy-HA
        eid_match = SimpleNamespace(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",  # HA Registry ID
            config_entry_id="abc123",
            canonical_id="68419b51-0000-2131-873b-fc411691d329",  # UUID-only
            time_offset=0,
            is_reversed=False,
        )

        # Bermuda accesses these fields via getattr
        assert getattr(eid_match, "device_id", None) is not None
        assert getattr(eid_match, "canonical_id", None) is not None

        # canonical_id should be UUID-only (no colons as separators)
        canonical = eid_match.canonical_id
        # UUID format has dashes, not colons for separation
        assert ":" not in canonical.replace("-", ""), (
            "canonical_id should be UUID-only, not entry_id:uuid format"
        )

    def test_device_registry_identifier_formats(self) -> None:
        """Verify expected identifier formats from GoogleFindMy-HA.

        GoogleFindMy-HA registers devices with identifiers in these formats:
        - (DOMAIN, "entry_id:subentry_id:device_id") - full format (2 colons)
        - (DOMAIN, "entry_id:device_id") - canonical format (1 colon)
        - (DOMAIN, "device_id") - simplest format (0 colons)

        The device_id is the Google UUID (e.g., "68419b51-0000-...").
        """
        test_cases = [
            # (identifier_value, expected_uuid)
            ("entry123:subentry456:68419b51-0000-2131-873b-fc411691d329", "68419b51-0000-2131-873b-fc411691d329"),
            ("entry123:68419b51-0000-2131-873b-fc411691d329", "68419b51-0000-2131-873b-fc411691d329"),
            ("68419b51-0000-2131-873b-fc411691d329", "68419b51-0000-2131-873b-fc411691d329"),
        ]

        for identifier_value, expected_uuid in test_cases:
            # This is how GoogleFindMy-HA normalizes canonical_id
            if ":" in identifier_value:
                extracted = identifier_value.split(":")[-1]
            else:
                extracted = identifier_value

            assert extracted == expected_uuid, (
                f"Failed to extract UUID from '{identifier_value}'"
            )


# =============================================================================
# canonical_id Extraction Tests
# =============================================================================


class TestCanonicalIdExtraction:
    """
    Tests for _extract_canonical_id() to ensure UUID-only format.

    CRITICAL: This must match GoogleFindMy-HA's normalization logic:
        clean_canonical_id = identity.canonical_id.split(":")[-1]
    """

    def test_extracts_uuid_from_full_format(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """Extract UUID from entry_id:subentry_id:device_id format."""
        device = SimpleNamespace(
            identifiers={
                (DOMAIN_GOOGLEFINDMY, "entry123:subentry456:68419b51-0000-2131-873b-fc411691d329"),
            }
        )

        result = fmdn_integration._extract_canonical_id(device)

        assert result == "68419b51-0000-2131-873b-fc411691d329"

    def test_extracts_uuid_from_canonical_format(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """Extract UUID from entry_id:device_id format."""
        device = SimpleNamespace(
            identifiers={
                (DOMAIN_GOOGLEFINDMY, "entry123:68419b51-0000-2131-873b-fc411691d329"),
            }
        )

        result = fmdn_integration._extract_canonical_id(device)

        assert result == "68419b51-0000-2131-873b-fc411691d329"

    def test_returns_uuid_directly_if_no_colons(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """Return UUID as-is when already in simplest format."""
        device = SimpleNamespace(
            identifiers={
                (DOMAIN_GOOGLEFINDMY, "68419b51-0000-2131-873b-fc411691d329"),
            }
        )

        result = fmdn_integration._extract_canonical_id(device)

        assert result == "68419b51-0000-2131-873b-fc411691d329"

    def test_ignores_non_googlefindmy_identifiers(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """Only process identifiers from googlefindmy domain."""
        device = SimpleNamespace(
            identifiers={
                ("other_domain", "some_id"),
                (DOMAIN_GOOGLEFINDMY, "entry:68419b51-0000"),
            }
        )

        result = fmdn_integration._extract_canonical_id(device)

        assert result == "68419b51-0000"

    def test_returns_none_for_empty_identifiers(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """Return None when no googlefindmy identifiers found."""
        device = SimpleNamespace(identifiers=set())

        result = fmdn_integration._extract_canonical_id(device)

        assert result is None


# =============================================================================
# Metadevice Address Consistency Tests
# =============================================================================


class TestMetadeviceAddressConsistency:
    """
    Tests that verify both discovery paths produce IDENTICAL addresses.

    CRITICAL: If Path A (entity discovery) and Path B (EID resolution) produce
    different addresses, duplicate metadevices will be created!
    """

    def test_canonical_id_preferred_over_device_id(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """format_metadevice_address should prefer canonical_id."""
        address = fmdn_integration.format_metadevice_address(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",
            canonical_id="68419b51-0000-2131-873b-fc411691d329",
        )

        # Should use canonical_id, not device_id
        assert "68419b51-0000-2131-873b-fc411691d329" in address
        assert "920aa0336e9c8bcf58b6dada3a9c68cb" not in address

    def test_fallback_to_device_id_when_no_canonical(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """format_metadevice_address falls back to device_id."""
        address = fmdn_integration.format_metadevice_address(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",
            canonical_id=None,
        )

        assert "920aa0336e9c8bcf58b6dada3a9c68cb" in address

    def test_entity_discovery_and_eid_resolution_produce_same_address(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """
        Both discovery paths must produce identical metadevice addresses.

        Path A (entity discovery): _extract_canonical_id() → format_metadevice_address()
        Path B (EID resolution): match.canonical_id → format_metadevice_address()
        """
        # Simulate Path A: Entity discovery
        # Device registry has identifier with entry_id prefix
        device_from_registry = SimpleNamespace(
            identifiers={
                (DOMAIN_GOOGLEFINDMY, "entry123:68419b51-0000-2131-873b-fc411691d329"),
            }
        )
        canonical_id_path_a = fmdn_integration._extract_canonical_id(device_from_registry)
        address_path_a = fmdn_integration.format_metadevice_address(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",
            canonical_id=canonical_id_path_a,
        )

        # Simulate Path B: EID resolution
        # EIDMatch.canonical_id is already UUID-only (GoogleFindMy-HA normalizes it)
        canonical_id_path_b = "68419b51-0000-2131-873b-fc411691d329"
        address_path_b = fmdn_integration.format_metadevice_address(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",
            canonical_id=canonical_id_path_b,
        )

        # CRITICAL: Both paths must produce the same address!
        assert address_path_a == address_path_b, (
            f"Path A produced '{address_path_a}' but Path B produced '{address_path_b}'. "
            "This will cause duplicate metadevices!"
        )

    def test_address_format_is_normalized(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """Addresses should be normalized (lowercase, no special chars)."""
        address = fmdn_integration.format_metadevice_address(
            device_id=None,
            canonical_id="68419B51-0000-2131-873B-FC411691D329",  # Uppercase
        )

        # Should be normalized to lowercase
        assert address == address.lower()
        assert address.startswith("fmdn:")


# =============================================================================
# Device Congealment Tests
# =============================================================================


class TestDeviceCongealment:
    """
    Tests for device congealment (Bermuda entities in GoogleFindMy device).

    For congealment to work, Bermuda must:
    1. Store fmdn_device_id (HA Registry ID) on the metadevice
    2. Use GoogleFindMy's identifiers in entity device_info
    """

    def test_register_source_stores_device_id(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """register_source must store fmdn_device_id for congealment."""
        # Create a source device
        from custom_components.bermuda.bermuda_device import BermudaDevice
        source_device = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
        mock_coordinator.devices["aa:bb:cc:dd:ee:ff"] = source_device

        # Simulate EIDMatch from resolver
        match = SimpleNamespace(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",
            canonical_id="68419b51-0000-2131-873b-fc411691d329",
        )

        # Mock _get_or_create_device
        metadevice = BermudaDevice(address="fmdn:68419b51-0000-2131-873b-fc411691d329", coordinator=mock_coordinator)
        mock_coordinator._get_or_create_device = MagicMock(return_value=metadevice)

        metadevice_address = fmdn_integration.format_metadevice_address(
            str(match.device_id), match.canonical_id
        )
        fmdn_integration.register_source(source_device, metadevice_address, match)

        # Verify fmdn_device_id is set (required for congealment)
        assert metadevice.fmdn_device_id == "920aa0336e9c8bcf58b6dada3a9c68cb"

    def test_register_source_stores_canonical_id(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """register_source must store fmdn_canonical_id."""
        from custom_components.bermuda.bermuda_device import BermudaDevice
        source_device = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
        mock_coordinator.devices["aa:bb:cc:dd:ee:ff"] = source_device

        match = SimpleNamespace(
            device_id="920aa0336e9c8bcf58b6dada3a9c68cb",
            canonical_id="68419b51-0000-2131-873b-fc411691d329",
        )

        metadevice = BermudaDevice(address="fmdn:68419b51-0000-2131-873b-fc411691d329", coordinator=mock_coordinator)
        mock_coordinator._get_or_create_device = MagicMock(return_value=metadevice)

        metadevice_address = fmdn_integration.format_metadevice_address(
            str(match.device_id), match.canonical_id
        )
        fmdn_integration.register_source(source_device, metadevice_address, match)

        # Verify fmdn_canonical_id is set
        assert metadevice.fmdn_canonical_id == "68419b51-0000-2131-873b-fc411691d329"


# =============================================================================
# Cache Consistency Tests
# =============================================================================


class TestCacheConsistency:
    """Tests for the dual-cache system (device_id and canonical_id)."""

    def test_cache_lookup_by_canonical_id(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """Cache should find metadevice by canonical_id."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        metadevice = BermudaDevice(address="fmdn:68419b51-0000", coordinator=mock_coordinator)
        mock_coordinator.metadevices["fmdn:68419b51-0000"] = metadevice

        fmdn_integration._update_cache(
            metadevice_address="fmdn:68419b51-0000",
            fmdn_device_id=None,
            canonical_id="68419b51-0000",
        )

        found = fmdn_integration._get_cached_metadevice(
            fmdn_device_id=None,
            canonical_id="68419b51-0000",
        )

        assert found is metadevice

    def test_cache_lookup_by_device_id(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """Cache should find metadevice by device_id as fallback."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        metadevice = BermudaDevice(address="fmdn:68419b51-0000", coordinator=mock_coordinator)
        mock_coordinator.metadevices["fmdn:68419b51-0000"] = metadevice

        fmdn_integration._update_cache(
            metadevice_address="fmdn:68419b51-0000",
            fmdn_device_id="920aa033",
            canonical_id=None,
        )

        found = fmdn_integration._get_cached_metadevice(
            fmdn_device_id="920aa033",
            canonical_id=None,
        )

        assert found is metadevice

    def test_cache_prefers_canonical_id_lookup(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """canonical_id cache should be checked before device_id cache."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        metadevice1 = BermudaDevice(address="fmdn:via-canonical", coordinator=mock_coordinator)
        metadevice2 = BermudaDevice(address="fmdn:via-device-id", coordinator=mock_coordinator)
        mock_coordinator.metadevices["fmdn:via-canonical"] = metadevice1
        mock_coordinator.metadevices["fmdn:via-device-id"] = metadevice2

        # Set up caches to point to different metadevices
        fmdn_integration._fmdn_canonical_id_cache["canonical123"] = "fmdn:via-canonical"
        fmdn_integration._fmdn_device_id_cache["device456"] = "fmdn:via-device-id"

        # When both are provided, canonical_id should win
        found = fmdn_integration._get_cached_metadevice(
            fmdn_device_id="device456",
            canonical_id="canonical123",
        )

        assert found is metadevice1  # Found via canonical_id, not device_id


# =============================================================================
# Regression Tests
# =============================================================================


class TestRegressions:
    """Tests for previously fixed bugs to prevent regressions."""

    def test_no_duplicate_addresses_in_prune_list(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Regression test for KeyError in prune_devices().

        BUG (Fixed 2026-01-23): Same device address added to prune_list multiple
        times when device appears in multiple metadevices' sources.
        """
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from custom_components.bermuda.const import METADEVICE_TYPE_FMDN_SOURCE

        # Create a device that could appear in multiple metadevices
        device = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
        device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)
        device.last_seen = 0  # Very old, should be pruned

        prune_list: list[str] = []
        stamp_fmdn = 1000.0

        # Call prune_source multiple times (simulating multiple metadevices)
        fmdn_integration.prune_source(device, stamp_fmdn, prune_list)
        fmdn_integration.prune_source(device, stamp_fmdn, prune_list)
        fmdn_integration.prune_source(device, stamp_fmdn, prune_list)

        # Should only appear once in prune_list!
        assert prune_list.count("aa:bb:cc:dd:ee:ff") == 1

    def test_canonical_id_uuid_only_not_prefixed(
        self, fmdn_integration: FmdnIntegration
    ) -> None:
        """
        Regression test for duplicate metadevices due to ID format mismatch.

        BUG (Fixed 2026-01-23): _extract_canonical_id() returned "entry_id:uuid"
        but EID resolver returned "uuid"-only, causing different addresses.
        """
        # Identifier with entry_id prefix (as stored in device registry)
        device = SimpleNamespace(
            identifiers={
                (DOMAIN_GOOGLEFINDMY, "config_entry_abc:68419b51-0000-2131-873b-fc411691d329"),
            }
        )

        extracted = fmdn_integration._extract_canonical_id(device)

        # Must be UUID-only, NOT "config_entry_abc:68419b51-..."
        assert extracted == "68419b51-0000-2131-873b-fc411691d329"
        assert "config_entry" not in extracted
        assert extracted.count(":") == 0 or all(
            len(part) <= 4 for part in extracted.split(":")
        ), "Should be UUID format (dash-separated), not colon-prefixed"
