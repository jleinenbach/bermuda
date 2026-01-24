"""Tests for typed EIDMatch in FMDN integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.bermuda.fmdn.integration import (
    EIDMatch,
    _convert_to_eid_match,
)
from custom_components.bermuda.fmdn.manager import BermudaFmdnManager, SeenEid


class TestEIDMatchNamedTuple:
    """Tests for the local EIDMatch type definition."""

    def test_eid_match_fields_accessible(self) -> None:
        """Verify all EIDMatch fields are accessible by name."""
        match = EIDMatch(
            device_id="test_device_id",
            config_entry_id="test_entry_id",
            canonical_id="test_canonical_id",
            time_offset=120,
            is_reversed=False,
        )
        assert match.device_id == "test_device_id"
        assert match.config_entry_id == "test_entry_id"
        assert match.canonical_id == "test_canonical_id"
        assert match.time_offset == 120
        assert match.is_reversed is False

    def test_eid_match_is_immutable(self) -> None:
        """Verify EIDMatch is immutable (NamedTuple behavior)."""
        match = EIDMatch(
            device_id="test",
            config_entry_id="entry",
            canonical_id="canonical",
            time_offset=0,
            is_reversed=False,
        )
        with pytest.raises(AttributeError):
            match.device_id = "new_value"  # type: ignore[misc]

    def test_eid_match_indexable(self) -> None:
        """Verify EIDMatch fields are accessible by index (tuple behavior)."""
        match = EIDMatch(
            device_id="device",
            config_entry_id="entry",
            canonical_id="canonical",
            time_offset=60,
            is_reversed=True,
        )
        # NamedTuple is also indexable
        assert match[0] == "device"
        assert match[1] == "entry"
        assert match[2] == "canonical"
        assert match[3] == 60
        assert match[4] is True


class TestConvertToEIDMatch:
    """Tests for the _convert_to_eid_match helper function."""

    def test_convert_from_external_match_all_fields(self) -> None:
        """Test conversion from external resolver match with all fields."""

        class ExternalMatch:
            device_id = "ext_device"
            config_entry_id = "ext_entry"
            canonical_id = "ext_canonical"
            time_offset = 60
            is_reversed = True

        external = ExternalMatch()
        local = _convert_to_eid_match(external)

        assert local is not None
        assert local.device_id == "ext_device"
        assert local.config_entry_id == "ext_entry"
        assert local.canonical_id == "ext_canonical"
        assert local.time_offset == 60
        assert local.is_reversed is True

    def test_convert_handles_missing_fields(self) -> None:
        """Test conversion when external match has missing fields."""

        class PartialMatch:
            device_id = "partial_device"
            # Missing other fields

        partial = PartialMatch()
        local = _convert_to_eid_match(partial)

        assert local is not None
        assert local.device_id == "partial_device"
        assert local.config_entry_id == ""  # Default
        assert local.canonical_id == ""  # Default
        assert local.time_offset == 0  # Default
        assert local.is_reversed is False  # Default

    def test_convert_returns_none_for_none_input(self) -> None:
        """Test that None input returns None."""
        result = _convert_to_eid_match(None)
        assert result is None

    def test_convert_handles_none_field_values(self) -> None:
        """Test conversion when fields have None values."""

        class NoneFieldsMatch:
            device_id = None
            config_entry_id = None
            canonical_id = None
            time_offset = None
            is_reversed = None

        match = NoneFieldsMatch()
        local = _convert_to_eid_match(match)

        assert local is not None
        assert local.device_id == ""
        assert local.config_entry_id == ""
        assert local.canonical_id == ""
        assert local.time_offset == 0
        assert local.is_reversed is False

    def test_convert_handles_mock_object(self) -> None:
        """Test conversion from MagicMock (simulates runtime resolver)."""
        mock_match = MagicMock()
        mock_match.device_id = "mock_device"
        mock_match.config_entry_id = "mock_entry"
        mock_match.canonical_id = "mock_canonical"
        mock_match.time_offset = 30
        mock_match.is_reversed = False

        local = _convert_to_eid_match(mock_match)

        assert local is not None
        assert local.device_id == "mock_device"
        assert local.time_offset == 30


class TestBermudaFmdnManagerDiagnostics:
    """Tests for diagnostic fields in BermudaFmdnManager."""

    def test_seen_eid_includes_diagnostic_fields(self) -> None:
        """Verify SeenEid stores diagnostic fields."""
        manager = BermudaFmdnManager()
        manager.record_resolution_success(
            eid=b"\x01\x02\x03",
            source_mac="AA:BB:CC:DD:EE:FF",
            device_id="test_device",
            canonical_id="test_canonical",
            time_offset=120,
            is_reversed=True,
        )

        diagnostics = manager.get_diagnostics_no_redactions()
        resolved = diagnostics["resolved_eids"]

        # Find our entry
        eid_hex = b"\x01\x02\x03".hex()
        assert eid_hex in resolved
        assert resolved[eid_hex]["time_offset"] == 120
        assert resolved[eid_hex]["is_reversed"] is True

    def test_diagnostics_omits_none_fields(self) -> None:
        """Verify None diagnostic fields are not included in output."""
        manager = BermudaFmdnManager()
        manager.record_resolution_success(
            eid=b"\x04\x05\x06",
            source_mac="AA:BB:CC:DD:EE:FF",
            device_id="test_device",
            # time_offset and is_reversed not provided (default None)
        )

        diagnostics = manager.get_diagnostics_no_redactions()
        resolved = diagnostics["resolved_eids"]
        eid_hex = b"\x04\x05\x06".hex()

        # Should NOT have these keys when None
        assert "time_offset" not in resolved[eid_hex]
        assert "is_reversed" not in resolved[eid_hex]

    def test_diagnostic_fields_update_on_subsequent_resolution(self) -> None:
        """Verify diagnostic fields update when EID is resolved again."""
        manager = BermudaFmdnManager()

        # First resolution without diagnostic fields
        manager.record_resolution_success(
            eid=b"\x07\x08\x09",
            source_mac="AA:BB:CC:DD:EE:FF",
            device_id="test_device",
        )

        # Second resolution with diagnostic fields
        manager.record_resolution_success(
            eid=b"\x07\x08\x09",
            source_mac="AA:BB:CC:DD:EE:FF",
            device_id="test_device",
            time_offset=45,
            is_reversed=True,
        )

        diagnostics = manager.get_diagnostics_no_redactions()
        resolved = diagnostics["resolved_eids"]
        eid_hex = b"\x07\x08\x09".hex()

        # Should have the updated diagnostic fields
        assert resolved[eid_hex]["time_offset"] == 45
        assert resolved[eid_hex]["is_reversed"] is True

    def test_seen_eid_dataclass_has_diagnostic_fields(self) -> None:
        """Verify SeenEid dataclass includes diagnostic fields."""
        seen = SeenEid(
            eid=b"\x01\x02\x03",
            first_seen=1000.0,
            last_seen=1000.0,
            source_mac="AA:BB:CC:DD:EE:FF",
            resolution_status="RESOLVED",
            device_id="test_device",
            canonical_id="test_canonical",
            time_offset=60,
            is_reversed=True,
        )

        assert seen.time_offset == 60
        assert seen.is_reversed is True

    def test_seen_eid_default_diagnostic_fields(self) -> None:
        """Verify SeenEid diagnostic fields default to None."""
        seen = SeenEid(
            eid=b"\x01\x02\x03",
            first_seen=1000.0,
            last_seen=1000.0,
            source_mac="AA:BB:CC:DD:EE:FF",
            resolution_status="RESOLVED",
        )

        assert seen.time_offset is None
        assert seen.is_reversed is None


class TestEIDMatchTypeInProtocol:
    """Tests verifying EIDMatch is used correctly in method signatures."""

    def test_eid_match_can_be_used_as_type_hint(self) -> None:
        """Verify EIDMatch works as a type hint."""

        def process_match(match: EIDMatch) -> str:
            return match.device_id

        test_match = EIDMatch(
            device_id="typed_device",
            config_entry_id="entry",
            canonical_id="canonical",
            time_offset=0,
            is_reversed=False,
        )

        result = process_match(test_match)
        assert result == "typed_device"

    def test_eid_match_list_type_hint(self) -> None:
        """Verify list[EIDMatch] works as a type hint."""

        def process_matches(matches: list[EIDMatch]) -> list[str]:
            return [m.device_id for m in matches]

        test_matches = [
            EIDMatch("device1", "entry1", "canon1", 0, False),
            EIDMatch("device2", "entry2", "canon2", 10, True),
        ]

        result = process_matches(test_matches)
        assert result == ["device1", "device2"]
