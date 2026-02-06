"""
Tests for scanner DeviceEntry preference logic (_is_better_entry).

When multiple DeviceEntries match a single BLE scanner (e.g., ESPHome's "mac"
entry, HA Bluetooth's auto-created entry, and a second BT entry with BLE MAC),
the classification loop must prefer entries with richer metadata (area_id,
name_by_user) over entries without.
"""

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.bermuda.bermuda_device import _is_better_entry


def _make_entry(
    *,
    area_id: str | None = None,
    name_by_user: str | None = None,
    name: str = "Default Name",
    entry_id: str = "test_id",
) -> dr.DeviceEntry:
    """Create a mock DeviceEntry with specified metadata."""
    entry = MagicMock(spec=dr.DeviceEntry)
    entry.area_id = area_id
    entry.name_by_user = name_by_user
    entry.name = name
    entry.id = entry_id
    return entry


class TestIsBetterEntry:
    """Tests for the _is_better_entry helper function."""

    def test_candidate_with_area_beats_current_without(self) -> None:
        """Entry with area_id should be preferred over entry without."""
        current = _make_entry(area_id=None, name_by_user=None)
        candidate = _make_entry(area_id="kitchen_id", name_by_user=None)
        assert _is_better_entry(candidate, current) is True

    def test_candidate_without_area_loses_to_current_with_area(self) -> None:
        """Entry without area_id should not replace entry with area_id."""
        current = _make_entry(area_id="kitchen_id", name_by_user=None)
        candidate = _make_entry(area_id=None, name_by_user=None)
        assert _is_better_entry(candidate, current) is False

    def test_both_have_area_candidate_has_name_by_user(self) -> None:
        """When both have area_id, prefer the one with name_by_user."""
        current = _make_entry(area_id="kitchen_id", name_by_user=None)
        candidate = _make_entry(area_id="office_id", name_by_user="My Scanner")
        assert _is_better_entry(candidate, current) is True

    def test_both_have_area_current_has_name_by_user(self) -> None:
        """When both have area_id, keep current if it has name_by_user."""
        current = _make_entry(area_id="kitchen_id", name_by_user="My Scanner")
        candidate = _make_entry(area_id="office_id", name_by_user=None)
        assert _is_better_entry(candidate, current) is False

    def test_both_have_area_and_name_by_user(self) -> None:
        """When both have area_id and name_by_user, keep current (no change)."""
        current = _make_entry(area_id="kitchen_id", name_by_user="Scanner A")
        candidate = _make_entry(area_id="office_id", name_by_user="Scanner B")
        assert _is_better_entry(candidate, current) is False

    def test_both_lack_area_candidate_has_name_by_user(self) -> None:
        """When neither has area_id, prefer the one with name_by_user."""
        current = _make_entry(area_id=None, name_by_user=None)
        candidate = _make_entry(area_id=None, name_by_user="My Scanner")
        assert _is_better_entry(candidate, current) is True

    def test_both_lack_area_current_has_name_by_user(self) -> None:
        """When neither has area_id, keep current if it has name_by_user."""
        current = _make_entry(area_id=None, name_by_user="My Scanner")
        candidate = _make_entry(area_id=None, name_by_user=None)
        assert _is_better_entry(candidate, current) is False

    def test_both_lack_area_and_name_by_user(self) -> None:
        """When both have no metadata, keep current (no change)."""
        current = _make_entry(area_id=None, name_by_user=None)
        candidate = _make_entry(area_id=None, name_by_user=None)
        assert _is_better_entry(candidate, current) is False

    def test_candidate_with_name_but_no_area_vs_current_with_area(self) -> None:
        """area_id takes precedence over name_by_user."""
        current = _make_entry(area_id="kitchen_id", name_by_user=None)
        candidate = _make_entry(area_id=None, name_by_user="My Scanner")
        assert _is_better_entry(candidate, current) is False

    def test_candidate_with_area_vs_current_with_name_only(self) -> None:
        """Entry with area_id preferred over entry with only name_by_user."""
        current = _make_entry(area_id=None, name_by_user="My Scanner")
        candidate = _make_entry(area_id="kitchen_id", name_by_user=None)
        assert _is_better_entry(candidate, current) is True


class TestScannerEntryPreferenceScenarios:
    """Integration-style tests simulating real scanner scenarios."""

    def test_esphome_entry_preferred_over_bt_auto_created(self) -> None:
        """ESPHome entry with area should win over BT auto-created entry.

        Simulates the common case where:
        - ESPHome creates a device entry with "mac" connection, area_id set by user
        - HA Bluetooth auto-creates a device entry with "bluetooth" connection, no area
        """
        bt_auto = _make_entry(
            area_id=None,
            name_by_user=None,
            name="AA:BB:CC:DD:EE:FF",
            entry_id="bt_auto_id",
        )
        esphome_entry = _make_entry(
            area_id="kitchen_id",
            name_by_user="Kitchen Proxy",
            name="esphome-kitchen",
            entry_id="esphome_id",
        )
        # If BT auto entry comes first, ESPHome should replace it
        assert _is_better_entry(esphome_entry, bt_auto) is True
        # If ESPHome comes first, BT auto should NOT replace it
        assert _is_better_entry(bt_auto, esphome_entry) is False

    def test_bt_with_area_preferred_over_bt_without(self) -> None:
        """When two BT entries exist (WiFi MAC and BLE MAC), prefer the one with area.

        Since ESPHome 2025.3.0, scanners can have two BT entries:
        - Old: bluetooth connection with WiFi MAC (may have area from before)
        - New: bluetooth connection with BLE MAC (auto-created, no area)
        """
        bt_old = _make_entry(
            area_id="office_id",
            name_by_user="Office Scanner",
            name="old-bt-entry",
            entry_id="bt_old_id",
        )
        bt_new = _make_entry(
            area_id=None,
            name_by_user=None,
            name="AA:BB:CC:DD:EE:02",
            entry_id="bt_new_id",
        )
        assert _is_better_entry(bt_old, bt_new) is True
        assert _is_better_entry(bt_new, bt_old) is False

    def test_three_entries_best_wins(self) -> None:
        """Simulate three DeviceEntries for one physical scanner.

        Entry A: ESPHome "mac" connection, area=Kitchen, name_by_user set
        Entry B: Old BT "bluetooth" WiFi MAC, area=Kitchen, no name_by_user
        Entry C: New BT "bluetooth" BLE MAC, no area, no name_by_user

        Classification should pick A or B for mac category, not C for bt.
        """
        entry_a = _make_entry(
            area_id="kitchen_id",
            name_by_user="Kitchen Proxy",
            entry_id="esphome_id",
        )
        entry_b = _make_entry(
            area_id="kitchen_id",
            name_by_user=None,
            entry_id="bt_old_id",
        )
        entry_c = _make_entry(
            area_id=None,
            name_by_user=None,
            entry_id="bt_new_id",
        )

        # For the "bluetooth" category, regardless of iteration order:
        # Starting with C, B should replace it (has area)
        assert _is_better_entry(entry_b, entry_c) is True
        # Starting with C, A should replace it (has area + name)
        assert _is_better_entry(entry_a, entry_c) is True
        # Starting with B, A should replace it (has name_by_user tiebreaker)
        assert _is_better_entry(entry_a, entry_b) is True
        # Starting with A, B should NOT replace it
        assert _is_better_entry(entry_b, entry_a) is False
        # Starting with A, C should NOT replace it
        assert _is_better_entry(entry_c, entry_a) is False
