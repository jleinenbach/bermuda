"""
Tests for scanner DeviceEntry preference logic (_is_better_entry).

When multiple DeviceEntries match a single BLE scanner (e.g., ESPHome's "mac"
entry, HA Bluetooth's auto-created entry, and a second BT entry with BLE MAC),
the classification loop must prefer entries with richer metadata (area_id,
name_by_user) over entries without.
"""

from __future__ import annotations

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


def _make_entry_with_connections(
    *,
    area_id: str | None = None,
    name_by_user: str | None = None,
    name: str = "Default Name",
    entry_id: str = "test_id",
    connections: set[tuple[str, str]] | None = None,
) -> dr.DeviceEntry:
    """Create a mock DeviceEntry with specified metadata and connections."""
    entry = MagicMock(spec=dr.DeviceEntry)
    entry.area_id = area_id
    entry.name_by_user = name_by_user
    entry.name = name
    entry.id = entry_id
    entry.connections = connections or set()
    return entry


def _run_classification_loop(
    entries: list[dr.DeviceEntry],
) -> dict[str, object]:
    """Simulate the inner classification loop from bermuda_device.py lines 452-465.

    Returns a dict with the classification results:
    - scanner_devreg_bt: selected BT DeviceEntry (or None)
    - scanner_devreg_mac: selected MAC DeviceEntry (or None)
    - scanner_devreg_bt_address: BT address (or None)
    - scanner_devreg_mac_address: MAC address (or None)
    """
    scanner_devreg_bt: dr.DeviceEntry | None = None
    scanner_devreg_mac: dr.DeviceEntry | None = None
    scanner_devreg_bt_address: str | None = None
    scanner_devreg_mac_address: str | None = None

    for devreg_device in entries:
        for conn in devreg_device.connections:
            if conn[0] == "bluetooth":
                if scanner_devreg_bt is None or _is_better_entry(devreg_device, scanner_devreg_bt):
                    scanner_devreg_bt = devreg_device
                    scanner_devreg_bt_address = conn[1].lower()
            if conn[0] == "mac":
                if scanner_devreg_mac is None or _is_better_entry(devreg_device, scanner_devreg_mac):
                    scanner_devreg_mac = devreg_device
                    scanner_devreg_mac_address = conn[1].lower()

    return {
        "scanner_devreg_bt": scanner_devreg_bt,
        "scanner_devreg_mac": scanner_devreg_mac,
        "scanner_devreg_bt_address": scanner_devreg_bt_address,
        "scanner_devreg_mac_address": scanner_devreg_mac_address,
    }


def _run_downstream_selection(
    result: dict[str, object],
) -> dict[str, object]:
    """Simulate the downstream priority logic from bermuda_device.py lines 493-542.

    Takes the output of _run_classification_loop and returns selected values:
    - area_id: final area_id (BT preferred, MAC fallback)
    - entry_id: final entry_id
    - unique_id: final unique_id (MAC preferred, BT fallback)
    - address_ble_mac: BLE MAC address
    - address_wifi_mac: WiFi MAC address
    - name_devreg: selected device name
    - name_by_user: selected user-given name
    """
    scanner_devreg_bt = result["scanner_devreg_bt"]
    scanner_devreg_mac = result["scanner_devreg_mac"]
    scanner_devreg_bt_address = result["scanner_devreg_bt_address"]
    scanner_devreg_mac_address = result["scanner_devreg_mac_address"]

    _area_id = None
    _bt_name = None
    _mac_name = None
    _bt_name_by_user = None
    _mac_name_by_user = None
    _entry_id = None

    if scanner_devreg_bt is not None:
        _area_id = scanner_devreg_bt.area_id
        _entry_id = scanner_devreg_bt.id
        _bt_name_by_user = scanner_devreg_bt.name_by_user
        _bt_name = scanner_devreg_bt.name
    if scanner_devreg_mac is not None:
        _area_id = _area_id or scanner_devreg_mac.area_id
        _entry_id = _entry_id or scanner_devreg_mac.id
        _mac_name = scanner_devreg_mac.name
        _mac_name_by_user = scanner_devreg_mac.name_by_user

    hascanner_source = "fallback_source"
    unique_id = scanner_devreg_mac_address or scanner_devreg_bt_address or hascanner_source
    address_ble_mac = scanner_devreg_bt_address or scanner_devreg_mac_address or hascanner_source
    address_wifi_mac = scanner_devreg_mac_address

    name_devreg = _mac_name or _bt_name
    name_by_user = _bt_name_by_user or _mac_name_by_user

    return {
        "area_id": _area_id,
        "entry_id": _entry_id,
        "unique_id": unique_id,
        "address_ble_mac": address_ble_mac,
        "address_wifi_mac": address_wifi_mac,
        "name_devreg": name_devreg,
        "name_by_user": name_by_user,
    }


class TestDualConnectionDeviceEntry:
    """Test Gap 1: DeviceEntry with both bluetooth and mac connections."""

    def test_single_entry_with_both_connections(self) -> None:
        """A single DeviceEntry with both bluetooth and mac should populate both slots.

        ESPHome entries typically have a "mac" connection. In rare configurations,
        a single entry might carry both connection types.
        """
        entry = _make_entry_with_connections(
            area_id="kitchen_id",
            name_by_user="Kitchen Proxy",
            name="esphome-kitchen",
            entry_id="dual_entry_id",
            connections={
                ("bluetooth", "AA:BB:CC:DD:EE:02"),
                ("mac", "AA:BB:CC:DD:EE:00"),
            },
        )

        result = _run_classification_loop([entry])

        # Same entry should be selected for BOTH categories
        assert result["scanner_devreg_bt"] is entry
        assert result["scanner_devreg_mac"] is entry
        assert result["scanner_devreg_bt_address"] == "aa:bb:cc:dd:ee:02"
        assert result["scanner_devreg_mac_address"] == "aa:bb:cc:dd:ee:00"

    def test_dual_connection_entry_vs_bt_only_entry(self) -> None:
        """Dual-connection entry with area should win over BT-only entry without."""
        bt_only = _make_entry_with_connections(
            area_id=None,
            name_by_user=None,
            name="AA:BB:CC:DD:EE:02",
            entry_id="bt_only_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )
        dual = _make_entry_with_connections(
            area_id="office_id",
            name_by_user="Office Proxy",
            name="esphome-office",
            entry_id="dual_id",
            connections={
                ("bluetooth", "AA:BB:CC:DD:EE:00"),
                ("mac", "AA:BB:CC:DD:EE:00"),
            },
        )

        # Order: BT-only first, then dual
        result = _run_classification_loop([bt_only, dual])
        assert result["scanner_devreg_bt"] is dual
        assert result["scanner_devreg_mac"] is dual

        # Order: dual first, then BT-only
        result = _run_classification_loop([dual, bt_only])
        assert result["scanner_devreg_bt"] is dual
        assert result["scanner_devreg_mac"] is dual

    def test_mac_address_lowercased(self) -> None:
        """MAC addresses from the mac connection type must be lowercased."""
        entry = _make_entry_with_connections(
            area_id="kitchen_id",
            name="esphome-kitchen",
            entry_id="entry_id",
            connections={("mac", "AA:BB:CC:DD:EE:FF")},
        )
        result = _run_classification_loop([entry])
        assert result["scanner_devreg_mac_address"] == "aa:bb:cc:dd:ee:ff"

    def test_bt_address_lowercased(self) -> None:
        """BT addresses from the bluetooth connection type must be lowercased."""
        entry = _make_entry_with_connections(
            area_id=None,
            name="AA:BB:CC:DD:EE:FF",
            entry_id="entry_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:FF")},
        )
        result = _run_classification_loop([entry])
        assert result["scanner_devreg_bt_address"] == "aa:bb:cc:dd:ee:ff"


class TestDownstreamAreaNameSelection:
    """Test Gap 2: Regression tests for downstream area_id/name selection logic.

    The priority logic (lines 500-542 in bermuda_device.py) follows these rules:
    - area_id: BT entry preferred, MAC entry as fallback (via `or`)
    - entry_id: BT entry preferred, MAC entry as fallback (via `or`)
    - name_devreg: MAC name preferred over BT name (via `or`)
    - name_by_user: BT name_by_user preferred, MAC as fallback (via `or`)
    - unique_id: MAC address preferred, BT as fallback
    - address_ble_mac: BT address preferred, MAC as fallback
    """

    def test_bt_area_preferred_over_mac_area(self) -> None:
        """BT entry's area_id should take priority over MAC entry's."""
        bt_entry = _make_entry_with_connections(
            area_id="bt_area_id",
            name_by_user="BT Scanner",
            name="bt-name",
            entry_id="bt_entry_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )
        mac_entry = _make_entry_with_connections(
            area_id="mac_area_id",
            name_by_user="MAC Scanner",
            name="mac-name",
            entry_id="mac_entry_id",
            connections={("mac", "AA:BB:CC:DD:EE:00")},
        )

        result = _run_classification_loop([bt_entry, mac_entry])
        downstream = _run_downstream_selection(result)

        # BT area_id wins (primary)
        assert downstream["area_id"] == "bt_area_id"
        # BT entry_id wins (primary)
        assert downstream["entry_id"] == "bt_entry_id"
        # MAC name preferred for name_devreg (mac_name or bt_name)
        assert downstream["name_devreg"] == "mac-name"
        # BT name_by_user preferred (bt_name_by_user or mac_name_by_user)
        assert downstream["name_by_user"] == "BT Scanner"

    def test_mac_area_used_when_bt_has_none(self) -> None:
        """MAC entry's area_id should be used when BT entry has no area."""
        bt_entry = _make_entry_with_connections(
            area_id=None,
            name_by_user=None,
            name="AA:BB:CC:DD:EE:02",
            entry_id="bt_entry_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )
        mac_entry = _make_entry_with_connections(
            area_id="kitchen_id",
            name_by_user="Kitchen Proxy",
            name="esphome-kitchen",
            entry_id="mac_entry_id",
            connections={("mac", "AA:BB:CC:DD:EE:00")},
        )

        result = _run_classification_loop([bt_entry, mac_entry])
        downstream = _run_downstream_selection(result)

        # BT has no area → MAC area used as fallback
        assert downstream["area_id"] == "kitchen_id"
        # BT entry_id still wins (it's not None)
        assert downstream["entry_id"] == "bt_entry_id"
        # MAC name preferred
        assert downstream["name_devreg"] == "esphome-kitchen"
        # MAC name_by_user used as fallback
        assert downstream["name_by_user"] == "Kitchen Proxy"

    def test_address_priority_unique_id_prefers_mac(self) -> None:
        """unique_id should prefer MAC address over BT address."""
        bt_entry = _make_entry_with_connections(
            area_id="area_id",
            name="bt-name",
            entry_id="bt_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )
        mac_entry = _make_entry_with_connections(
            area_id="area_id",
            name="mac-name",
            entry_id="mac_id",
            connections={("mac", "AA:BB:CC:DD:EE:00")},
        )

        result = _run_classification_loop([bt_entry, mac_entry])
        downstream = _run_downstream_selection(result)

        # unique_id = mac_address or bt_address
        assert downstream["unique_id"] == "aa:bb:cc:dd:ee:00"
        # address_ble_mac = bt_address or mac_address
        assert downstream["address_ble_mac"] == "aa:bb:cc:dd:ee:02"
        # address_wifi_mac = mac_address only
        assert downstream["address_wifi_mac"] == "aa:bb:cc:dd:ee:00"

    def test_bt_only_no_mac_entry(self) -> None:
        """When only a BT entry exists, all fields should come from BT."""
        bt_entry = _make_entry_with_connections(
            area_id="office_id",
            name_by_user="Office Scanner",
            name="bt-office",
            entry_id="bt_entry_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )

        result = _run_classification_loop([bt_entry])
        downstream = _run_downstream_selection(result)

        assert downstream["area_id"] == "office_id"
        assert downstream["entry_id"] == "bt_entry_id"
        assert downstream["name_devreg"] == "bt-office"
        assert downstream["name_by_user"] == "Office Scanner"
        # No MAC → BT address used for all
        assert downstream["unique_id"] == "aa:bb:cc:dd:ee:02"
        assert downstream["address_ble_mac"] == "aa:bb:cc:dd:ee:02"
        assert downstream["address_wifi_mac"] is None

    def test_mac_only_no_bt_entry(self) -> None:
        """When only a MAC entry exists, all fields should come from MAC."""
        mac_entry = _make_entry_with_connections(
            area_id="kitchen_id",
            name_by_user="Kitchen Proxy",
            name="esphome-kitchen",
            entry_id="mac_entry_id",
            connections={("mac", "AA:BB:CC:DD:EE:00")},
        )

        result = _run_classification_loop([mac_entry])
        downstream = _run_downstream_selection(result)

        assert downstream["area_id"] == "kitchen_id"
        assert downstream["entry_id"] == "mac_entry_id"
        assert downstream["name_devreg"] == "esphome-kitchen"
        assert downstream["name_by_user"] == "Kitchen Proxy"
        assert downstream["unique_id"] == "aa:bb:cc:dd:ee:00"
        assert downstream["address_ble_mac"] == "aa:bb:cc:dd:ee:00"
        assert downstream["address_wifi_mac"] == "aa:bb:cc:dd:ee:00"

    def test_three_entries_correct_downstream_selection(self) -> None:
        """Full scenario: ESPHome + old BT + new BT auto-created entry.

        ESPHome: "mac" connection, area=Kitchen, name_by_user="Kitchen Proxy"
        Old BT: "bluetooth" connection (WiFi MAC), area=Kitchen, no name_by_user
        New BT: "bluetooth" connection (BLE MAC), no area, no name_by_user

        Expected: ESPHome wins for MAC, Old BT wins for BT (has area).
        Downstream: area from BT (Kitchen), name from MAC (esphome-kitchen).
        """
        esphome_entry = _make_entry_with_connections(
            area_id="kitchen_id",
            name_by_user="Kitchen Proxy",
            name="esphome-kitchen",
            entry_id="esphome_id",
            connections={("mac", "AA:BB:CC:DD:EE:00")},
        )
        bt_old = _make_entry_with_connections(
            area_id="kitchen_id",
            name_by_user=None,
            name="old-bt",
            entry_id="bt_old_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:00")},
        )
        bt_new = _make_entry_with_connections(
            area_id=None,
            name_by_user=None,
            name="AA:BB:CC:DD:EE:02",
            entry_id="bt_new_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )

        # Test all iteration orders — result should be the same
        for order in [
            [esphome_entry, bt_old, bt_new],
            [bt_new, bt_old, esphome_entry],
            [bt_old, bt_new, esphome_entry],
            [bt_new, esphome_entry, bt_old],
            [esphome_entry, bt_new, bt_old],
            [bt_old, esphome_entry, bt_new],
        ]:
            result = _run_classification_loop(order)
            downstream = _run_downstream_selection(result)

            # BT old wins for bluetooth (has area_id, bt_new doesn't)
            assert result["scanner_devreg_bt"] is bt_old, f"BT selection wrong for order {[e.id for e in order]}"
            # ESPHome wins for mac (only mac entry)
            assert result["scanner_devreg_mac"] is esphome_entry

            # BT area_id preferred (Kitchen from bt_old)
            assert downstream["area_id"] == "kitchen_id"
            # MAC name preferred (esphome-kitchen from esphome_entry)
            assert downstream["name_devreg"] == "esphome-kitchen"
            # MAC name_by_user as fallback (bt_old has None)
            assert downstream["name_by_user"] == "Kitchen Proxy"
            # MAC address preferred for unique_id
            assert downstream["unique_id"] == "aa:bb:cc:dd:ee:00"
            # BT address preferred for BLE MAC
            assert downstream["address_ble_mac"] == "aa:bb:cc:dd:ee:00"

    def test_no_area_anywhere_clears_area(self) -> None:
        """When neither BT nor MAC entry has area_id, area should be None."""
        bt_entry = _make_entry_with_connections(
            area_id=None,
            name_by_user=None,
            name="AA:BB:CC:DD:EE:02",
            entry_id="bt_id",
            connections={("bluetooth", "AA:BB:CC:DD:EE:02")},
        )
        mac_entry = _make_entry_with_connections(
            area_id=None,
            name_by_user=None,
            name="esphome-unnamed",
            entry_id="mac_id",
            connections={("mac", "AA:BB:CC:DD:EE:00")},
        )

        result = _run_classification_loop([bt_entry, mac_entry])
        downstream = _run_downstream_selection(result)

        assert downstream["area_id"] is None
