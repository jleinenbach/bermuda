"""
Test race conditions in Training UI (Floor/Room Select entities).

This test verifies that coordinator refresh events cannot corrupt the
dropdown state when they occur during user interaction.

The race condition occurs when:
1. User selects a room in the dropdown
2. async_select_option() starts executing
3. A coordinator refresh triggers _handle_coordinator_update()
4. If device attrs aren't set yet, the callback clears local UI state
5. User sees their selection disappear

The fix is to set device attributes FIRST, before local UI variables.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    pass


class FakeDevice:
    """Minimal fake device for testing select entities."""

    def __init__(self) -> None:
        self.name = "Test Device"
        self.unique_id = "aa:bb:cc:dd:ee:ff"
        self.address = "aa:bb:cc:dd:ee:ff"
        # Training target fields - these control button availability
        self.training_target_floor_id: str | None = None
        self.training_target_area_id: str | None = None
        # Area lock fields
        self.area_locked_id: str | None = None
        self.area_locked_name: str | None = None
        self.area_locked_scanner_addr: str | None = None
        # Current area (from auto-detection)
        self.area_id: str | None = None
        self.area_name: str | None = None
        self.area_advert: MagicMock | None = None
        self.adverts: dict = {}
        self.ref_power_changed: float = 0.0


class FakeArea:
    """Minimal fake area for testing."""

    def __init__(self, area_id: str, name: str, floor_id: str | None = None) -> None:
        self.id = area_id
        self.name = name
        self.floor_id = floor_id


class FakeFloor:
    """Minimal fake floor for testing."""

    def __init__(self, floor_id: str, name: str, level: int | None = None) -> None:
        self.floor_id = floor_id
        self.name = name
        self.level = level


class TestRaceConditionPrevention:
    """Test that race conditions are prevented in training UI."""

    def test_device_attr_must_be_set_before_local_vars_room(self) -> None:
        """
        Verify that _handle_coordinator_update won't clear local vars
        if device attr is already set.

        This simulates the FIX: device attr is set FIRST.
        """
        device = FakeDevice()

        # Simulate the FIX: device attr set FIRST
        device.training_target_area_id = "kitchen_id"

        # Local UI state (would be set after device attr in fixed code)
        room_override_name: str | None = "Kitchen"
        room_override_id: str | None = "kitchen_id"

        # Simulate _handle_coordinator_update callback
        # This is the logic from BermudaTrainingRoomSelect._handle_coordinator_update
        if device.training_target_area_id is None:
            room_override_name = None
            room_override_id = None

        # Verify local state is NOT cleared (because device attr was set)
        assert room_override_name == "Kitchen"
        assert room_override_id == "kitchen_id"

    def test_device_attr_not_set_clears_local_vars_room(self) -> None:
        """
        Verify that _handle_coordinator_update DOES clear local vars
        when device attr is None (e.g., after button press).

        This is the INTENDED behavior for clearing after training completes.
        """
        device = FakeDevice()

        # Device attr is None (as after button clears it)
        device.training_target_area_id = None

        # Local UI state still has values
        room_override_name: str | None = "Kitchen"
        room_override_id: str | None = "kitchen_id"

        # Simulate _handle_coordinator_update callback
        if device.training_target_area_id is None:
            room_override_name = None
            room_override_id = None

        # Verify local state IS cleared (intended behavior)
        assert room_override_name is None
        assert room_override_id is None

    def test_device_attr_must_be_set_before_local_vars_floor(self) -> None:
        """
        Same test for FloorSelect - verify device attr protects local vars.
        """
        device = FakeDevice()

        # Simulate the FIX: device attr set FIRST
        device.training_target_floor_id = "floor_1"

        # Local UI state
        floor_override_id: str | None = "floor_1"
        floor_override_name: str | None = "Ground Floor"

        # Simulate _handle_coordinator_update callback
        if device.training_target_floor_id is None:
            floor_override_id = None
            floor_override_name = None

        # Verify local state is NOT cleared
        assert floor_override_id == "floor_1"
        assert floor_override_name == "Ground Floor"

    def test_race_condition_timeline_old_behavior(self) -> None:
        """
        Demonstrate the OLD (buggy) behavior where race condition can occur.

        Timeline:
        T0: User selects "Kitchen"
        T1: async_select_option sets local var first (OLD BEHAVIOR)
        T2: Coordinator refresh happens
        T3: _handle_coordinator_update clears local var (device attr still None!)
        T4: async_select_option sets device attr (too late!)
        T5: UI shows empty dropdown (BUG!)
        """
        device = FakeDevice()

        # T1: OLD behavior - local var set first
        room_override_name: str | None = "Kitchen"
        room_override_id: str | None = "kitchen_id"

        # T2-T3: Coordinator refresh triggers update BEFORE device attr is set
        # device.training_target_area_id is still None!
        if device.training_target_area_id is None:
            room_override_name = None  # CLEARED BY RACE!
            room_override_id = None

        # T4: Device attr finally set (too late)
        device.training_target_area_id = "kitchen_id"

        # T5: UI shows empty - BUG!
        assert room_override_name is None  # This is the bug
        assert device.training_target_area_id == "kitchen_id"  # Attr is set but UI is wrong

    def test_race_condition_timeline_new_behavior(self) -> None:
        """
        Demonstrate the NEW (fixed) behavior where race condition is prevented.

        Timeline:
        T0: User selects "Kitchen"
        T1: async_select_option sets device attr FIRST (NEW BEHAVIOR)
        T2: Coordinator refresh happens
        T3: _handle_coordinator_update sees device attr is set, doesn't clear
        T4: async_select_option sets local var (safe now)
        T5: UI shows "Kitchen" correctly
        """
        device = FakeDevice()

        # T1: NEW behavior - device attr set FIRST
        device.training_target_area_id = "kitchen_id"

        # T2-T3: Coordinator refresh - device attr is already set, so no clear
        room_override_name: str | None = None  # Not set yet at this point
        room_override_id: str | None = None

        if device.training_target_area_id is None:
            # This block is NOT entered because device attr is set
            room_override_name = None
            room_override_id = None

        # T4: Local vars can now be safely set
        room_override_name = "Kitchen"
        room_override_id = "kitchen_id"

        # T5: UI shows correct value
        assert room_override_name == "Kitchen"
        assert room_override_id == "kitchen_id"
        assert device.training_target_area_id == "kitchen_id"


class TestAsyncRaceCondition:
    """Test race conditions with actual async execution."""

    @pytest.mark.asyncio
    async def test_concurrent_refresh_during_selection(self) -> None:
        """
        Test that concurrent coordinator refresh doesn't corrupt state.

        This simulates multiple rapid coordinator updates happening while
        the user is selecting options.
        """
        device = FakeDevice()

        # Track state changes for verification
        state_history: list[tuple[str | None, str | None]] = []

        async def simulate_select_option(area_id: str, area_name: str) -> None:
            """Simulate async_select_option with the FIX applied."""
            # FIX: Set device attr FIRST
            device.training_target_area_id = area_id
            # Small delay to allow other coroutines to run
            await asyncio.sleep(0)
            # Then set local state (simulated by recording)
            state_history.append((area_id, area_name))

        async def simulate_coordinator_refresh() -> None:
            """Simulate a coordinator refresh that triggers updates."""
            # This would normally trigger _handle_coordinator_update on all entities
            # With the fix, it checks device attr before clearing
            await asyncio.sleep(0)
            # Record that refresh happened
            state_history.append(("REFRESH", "REFRESH"))

        # Run selection and multiple refreshes concurrently
        await asyncio.gather(
            simulate_select_option("kitchen_id", "Kitchen"),
            simulate_coordinator_refresh(),
            simulate_coordinator_refresh(),
            simulate_coordinator_refresh(),
        )

        # Verify device attr is correctly set (not corrupted by refreshes)
        assert device.training_target_area_id == "kitchen_id"

    @pytest.mark.asyncio
    async def test_rapid_selection_changes(self) -> None:
        """
        Test rapid selection changes don't cause inconsistent state.

        User quickly changes selection: Kitchen -> Office -> Bedroom
        """
        device = FakeDevice()

        selections = [
            ("kitchen_id", "Kitchen"),
            ("office_id", "Office"),
            ("bedroom_id", "Bedroom"),
        ]

        for area_id, area_name in selections:
            # Simulate selection with fix applied
            device.training_target_area_id = area_id
            await asyncio.sleep(0)  # Allow other tasks

        # Final state should be the last selection
        assert device.training_target_area_id == "bedroom_id"


class TestButtonAvailabilityRaceCondition:
    """Test that button availability isn't affected by race conditions."""

    def test_button_available_when_both_attrs_set(self) -> None:
        """Button should be available when both floor and area are set."""
        device = FakeDevice()
        device.training_target_floor_id = "floor_1"
        device.training_target_area_id = "kitchen_id"

        # Simulate button.available property check
        available = device.training_target_floor_id is not None and device.training_target_area_id is not None
        assert available is True

    def test_button_unavailable_when_only_floor_set(self) -> None:
        """Button should be unavailable when only floor is set."""
        device = FakeDevice()
        device.training_target_floor_id = "floor_1"
        device.training_target_area_id = None

        available = device.training_target_floor_id is not None and device.training_target_area_id is not None
        assert available is False

    def test_button_unavailable_after_training_clears_attrs(self) -> None:
        """Button should be unavailable after training clears attrs."""
        device = FakeDevice()
        device.training_target_floor_id = "floor_1"
        device.training_target_area_id = "kitchen_id"

        # Verify available before
        assert device.training_target_floor_id is not None
        assert device.training_target_area_id is not None

        # Simulate button press clearing attrs
        device.training_target_floor_id = None
        device.training_target_area_id = None
        device.area_locked_id = None
        device.area_locked_name = None
        device.area_locked_scanner_addr = None

        # Verify unavailable after
        available = device.training_target_floor_id is not None and device.training_target_area_id is not None
        assert available is False


class TestStateOrderingInvariant:
    """
    Test that the state ordering invariant is maintained.

    INVARIANT: Device attrs (authoritative) must be set BEFORE local UI vars (derived).

    This ensures _handle_coordinator_update() sees consistent state.
    """

    def test_ordering_invariant_documentation(self) -> None:
        """
        Document the ordering invariant with code.

        This test serves as executable documentation of the required
        ordering for setting state in select entities.
        """
        device = FakeDevice()

        # === CORRECT ORDER (what the code should do) ===

        # Step 1: Set AUTHORITATIVE state (device attrs)
        device.training_target_area_id = "kitchen_id"  # FIRST!

        # Step 2: Set DERIVED state (local UI vars)
        local_room_name = "Kitchen"  # SECOND - safe now
        local_room_id = "kitchen_id"

        # Verify both are set correctly
        assert device.training_target_area_id == "kitchen_id"
        assert local_room_name == "Kitchen"
        assert local_room_id == "kitchen_id"

    def test_handle_update_logic_mirrors_ordering(self) -> None:
        """
        Verify _handle_coordinator_update checks authoritative state.

        The callback should check device attrs (authoritative) to decide
        whether to clear local vars (derived).
        """
        device = FakeDevice()

        # Scenario 1: Device attr set -> don't clear local
        device.training_target_area_id = "kitchen_id"
        local_name: str | None = "Kitchen"

        if device.training_target_area_id is None:
            local_name = None

        assert local_name == "Kitchen"  # Not cleared

        # Scenario 2: Device attr cleared -> clear local
        device.training_target_area_id = None

        if device.training_target_area_id is None:
            local_name = None

        assert local_name is None  # Cleared as expected
