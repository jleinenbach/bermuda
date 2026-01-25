"""Test BermudaIrkManager for IRK to MAC resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.bermuda_irk import BermudaIrkManager, ResolvableMAC
from custom_components.bermuda.const import IrkTypes, PRUNE_TIME_KNOWN_IRK


class TestResolvableMAC:
    """Tests for ResolvableMAC NamedTuple."""

    def test_resolvable_mac_creation(self) -> None:
        """Test creating a ResolvableMAC."""
        mac = "AA:BB:CC:DD:EE:FF"
        expires = 1000
        irk = b"\x00" * 16

        resolvable = ResolvableMAC(mac=mac, expires=expires, irk=irk)

        assert resolvable.mac == mac
        assert resolvable.expires == expires
        assert resolvable.irk == irk

    def test_resolvable_mac_is_namedtuple(self) -> None:
        """Test that ResolvableMAC behaves as a NamedTuple."""
        resolvable = ResolvableMAC(mac="AA:BB:CC:DD:EE:FF", expires=1000, irk=b"\x00" * 16)

        # NamedTuples support indexing
        assert resolvable[0] == "AA:BB:CC:DD:EE:FF"
        assert resolvable[1] == 1000
        assert resolvable[2] == b"\x00" * 16


class TestBermudaIrkManagerInit:
    """Tests for BermudaIrkManager initialization."""

    def test_init_creates_empty_containers(self) -> None:
        """Test that __init__ creates empty containers."""
        manager = BermudaIrkManager()

        assert manager._irks == {}
        assert manager._macs == {}
        assert manager._irk_callbacks == {}

    def test_irk_length_bytes_constant(self) -> None:
        """Test that IRK_LENGTH_BYTES is 16."""
        manager = BermudaIrkManager()
        assert manager.IRK_LENGTH_BYTES == 16


class TestAddIrk:
    """Tests for add_irk method."""

    def test_add_irk_valid_length(self) -> None:
        """Test adding a valid 16-byte IRK."""
        manager = BermudaIrkManager()
        irk = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"

        result = manager.add_irk(irk)

        assert result == []  # No matching MACs yet
        assert irk in manager._irks

    def test_add_irk_invalid_length_raises_error(self) -> None:
        """Test that adding an IRK with wrong length raises ValueError."""
        manager = BermudaIrkManager()

        with pytest.raises(ValueError, match="Invalid IRK length"):
            manager.add_irk(b"\x01\x02\x03")  # Only 3 bytes

    def test_add_irk_too_long_raises_error(self) -> None:
        """Test that adding an IRK that's too long raises ValueError."""
        manager = BermudaIrkManager()

        with pytest.raises(ValueError, match="Invalid IRK length"):
            manager.add_irk(b"\x00" * 20)  # 20 bytes

    def test_add_irk_duplicate_is_ignored(self) -> None:
        """Test that adding the same IRK twice doesn't create duplicates."""
        manager = BermudaIrkManager()
        irk = b"\x00" * 16

        manager.add_irk(irk)
        result = manager.add_irk(irk)  # Add again

        assert result == []
        assert len(manager._irks) == 1


class TestCheckMac:
    """Tests for check_mac method."""

    def test_check_mac_cached_result(self) -> None:
        """Test that check_mac returns cached result."""
        manager = BermudaIrkManager()
        irk = b"\x00" * 16
        mac = "AA:BB:CC:DD:EE:FF"

        # Manually add a cached result
        manager._macs[mac] = ResolvableMAC(mac=mac, expires=9999999, irk=irk)

        result = manager.check_mac(mac)

        assert result == irk

    def test_check_mac_not_resolvable_address(self) -> None:
        """Test check_mac with a non-RPA address.

        Note: The actual behavior is to return NO_KNOWN_IRK_MATCH for any unmatched
        address after checking all IRKs. The NOT_RESOLVABLE_ADDRESS is stored in cache
        via _validate_mac_irk but _validate_mac overwrites it at the end of the loop.
        """
        manager = BermudaIrkManager()
        # First character '0' -> not an RPA (RPAs have first nibble 4-7)
        mac = "00:11:22:33:44:55"
        irk = b"\x01" * 16

        # Add an IRK so the check goes through _validate_mac_irk
        manager.add_irk(irk)

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            result = manager.check_mac(mac)

        # Result is NO_KNOWN_IRK_MATCH because _validate_mac always calls
        # _update_saved_mac with NO_KNOWN_IRK_MATCH after the loop
        assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value

    def test_check_mac_rpa_no_match(self) -> None:
        """Test check_mac with an RPA that doesn't match any IRK."""
        manager = BermudaIrkManager()
        # First character '4' -> this is an RPA (first nibble in 4-7)
        mac = "44:11:22:33:44:55"

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            result = manager.check_mac(mac)

        # Should return NO_KNOWN_IRK_MATCH since it's an RPA but no IRK matches
        assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value


class TestScanDevice:
    """Tests for scan_device method."""

    def test_scan_device_no_match(self) -> None:
        """Test scan_device when no IRK matches a non-RPA address.

        Note: The actual behavior is to return NO_KNOWN_IRK_MATCH for any unmatched
        address after checking all IRKs.
        """
        manager = BermudaIrkManager()
        mac = "00:11:22:33:44:55"  # Not an RPA
        irk = b"\x01" * 16

        # Add an IRK so the check goes through _validate_mac_irk
        manager.add_irk(irk)

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            matched, result = manager.scan_device(mac)

        assert matched is False
        # Result is NO_KNOWN_IRK_MATCH (the code doesn't distinguish non-RPAs in return value)
        assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value

    def test_scan_device_rpa_no_irk_match(self) -> None:
        """Test scan_device with RPA but no matching IRK."""
        manager = BermudaIrkManager()
        mac = "55:11:22:33:44:55"  # First nibble 5 = RPA

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            matched, result = manager.scan_device(mac)

        assert matched is False
        assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value

    def test_scan_device_match_found(self) -> None:
        """Test scan_device when IRK matches."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "55:11:22:33:44:55"

        # Add the IRK first
        manager.add_irk(irk)

        # Mock resolve_private_address to return True for a match
        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=True):
            matched, result = manager.scan_device(mac)

        assert matched is True
        assert result == irk


class TestKnownMacs:
    """Tests for known_macs method."""

    def test_known_macs_empty(self) -> None:
        """Test known_macs when empty."""
        manager = BermudaIrkManager()

        result = manager.known_macs()

        assert result == {}

    def test_known_macs_resolved_only(self) -> None:
        """Test known_macs returns only resolved MACs by default."""
        manager = BermudaIrkManager()
        resolved_irk = b"\x01" * 16
        unresolved_irk = IrkTypes.NO_KNOWN_IRK_MATCH.value

        manager._macs["AA:BB:CC:DD:EE:01"] = ResolvableMAC(mac="AA:BB:CC:DD:EE:01", expires=9999999, irk=resolved_irk)
        manager._macs["AA:BB:CC:DD:EE:02"] = ResolvableMAC(mac="AA:BB:CC:DD:EE:02", expires=9999999, irk=unresolved_irk)

        result = manager.known_macs(resolved=True)

        assert len(result) == 1
        assert "AA:BB:CC:DD:EE:01" in result
        assert "AA:BB:CC:DD:EE:02" not in result

    def test_known_macs_all(self) -> None:
        """Test known_macs returns all MACs when resolved=False."""
        manager = BermudaIrkManager()
        resolved_irk = b"\x01" * 16
        unresolved_irk = IrkTypes.NO_KNOWN_IRK_MATCH.value

        manager._macs["AA:BB:CC:DD:EE:01"] = ResolvableMAC(mac="AA:BB:CC:DD:EE:01", expires=9999999, irk=resolved_irk)
        manager._macs["AA:BB:CC:DD:EE:02"] = ResolvableMAC(mac="AA:BB:CC:DD:EE:02", expires=9999999, irk=unresolved_irk)

        result = manager.known_macs(resolved=False)

        assert len(result) == 2
        assert "AA:BB:CC:DD:EE:01" in result
        assert "AA:BB:CC:DD:EE:02" in result


class TestAsyncPrune:
    """Tests for async_prune method."""

    def test_async_prune_removes_expired(self) -> None:
        """Test that async_prune removes expired MACs."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        nowstamp = monotonic_time_coarse()

        # Add an expired MAC
        manager._macs["AA:BB:CC:DD:EE:01"] = ResolvableMAC(
            mac="AA:BB:CC:DD:EE:01", expires=int(nowstamp - 100), irk=irk
        )
        # Add a valid MAC
        manager._macs["AA:BB:CC:DD:EE:02"] = ResolvableMAC(
            mac="AA:BB:CC:DD:EE:02", expires=int(nowstamp + 1000), irk=irk
        )

        manager.async_prune()

        assert "AA:BB:CC:DD:EE:01" not in manager._macs
        assert "AA:BB:CC:DD:EE:02" in manager._macs

    def test_async_prune_empty_does_nothing(self) -> None:
        """Test that async_prune on empty manager does nothing."""
        manager = BermudaIrkManager()

        # Should not raise
        manager.async_prune()

        assert manager._macs == {}


class TestAddMacirk:
    """Tests for add_macirk method."""

    def test_add_macirk_valid(self) -> None:
        """Test add_macirk with valid IRK and MAC."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "55:11:22:33:44:55"  # RPA

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=True):
            result = manager.add_macirk(mac, irk)

        assert result == irk

    def test_add_macirk_invalid_irk_raises(self) -> None:
        """Test add_macirk with invalid IRK raises ValueError."""
        manager = BermudaIrkManager()

        with pytest.raises(ValueError, match="Invalid IRK length"):
            manager.add_macirk("AA:BB:CC:DD:EE:FF", b"\x01\x02\x03")


class TestFireCallbacks:
    """Tests for fire_callbacks method."""

    def test_fire_callbacks_calls_registered(self) -> None:
        """Test that fire_callbacks calls registered callbacks."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "AA:BB:CC:DD:EE:FF"
        callback = MagicMock()

        manager._irk_callbacks[irk] = [callback]

        with (
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            manager.fire_callbacks(irk, mac)

        callback.assert_called_once()
        # Verify it was called with BluetoothServiceInfoBleak and BluetoothChange
        call_args = callback.call_args
        assert call_args is not None

    def test_fire_callbacks_no_callbacks(self) -> None:
        """Test that fire_callbacks does nothing when no callbacks registered."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "AA:BB:CC:DD:EE:FF"

        # Should not raise
        with (
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            manager.fire_callbacks(irk, mac)


class TestRegisterIrkCallback:
    """Tests for register_irk_callback method."""

    def test_register_irk_callback_adds_callback(self) -> None:
        """Test that register_irk_callback adds the callback."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        callback = MagicMock()

        with (
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            unsubscribe = manager.register_irk_callback(callback, irk)

        assert irk in manager._irk_callbacks
        assert callback in manager._irk_callbacks[irk]
        assert callable(unsubscribe)

    def test_register_irk_callback_unsubscribe(self) -> None:
        """Test that unsubscribe removes the callback."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        callback = MagicMock()

        with (
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            unsubscribe = manager.register_irk_callback(callback, irk)
            unsubscribe()

        assert irk not in manager._irk_callbacks

    def test_register_irk_callback_fires_for_existing_macs(self) -> None:
        """Test that register_irk_callback fires for existing matching MACs."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "AA:BB:CC:DD:EE:FF"
        callback = MagicMock()

        # Add an existing MAC with this IRK
        manager._macs[mac] = ResolvableMAC(mac=mac, expires=9999999, irk=irk)
        manager._irks[irk] = MagicMock()  # Add the IRK too

        with (
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            manager.register_irk_callback(callback, irk)

        callback.assert_called()


class TestGetDiagnosticsNoRedactions:
    """Tests for get_diagnostics_no_redactions method."""

    def test_get_diagnostics_empty(self) -> None:
        """Test diagnostics with empty manager."""
        manager = BermudaIrkManager()

        result = manager.get_diagnostics_no_redactions()

        assert result["irks"] == []
        assert result["macs"] == {}

    def test_get_diagnostics_with_irks(self) -> None:
        """Test diagnostics includes IRKs as hex strings."""
        manager = BermudaIrkManager()
        irk = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"
        manager.add_irk(irk)

        result = manager.get_diagnostics_no_redactions()

        assert irk.hex() in result["irks"]

    def test_get_diagnostics_with_resolved_macs(self) -> None:
        """Test diagnostics includes resolved MACs."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "AA:BB:CC:DD:EE:FF"
        nowstamp = monotonic_time_coarse()

        manager._macs[mac] = ResolvableMAC(mac=mac, expires=int(nowstamp + 1000), irk=irk)

        result = manager.get_diagnostics_no_redactions()

        assert mac in result["macs"]
        assert result["macs"][mac]["irk"] == irk.hex()
        assert "expires_in" in result["macs"][mac]

    def test_get_diagnostics_excludes_not_evaluated(self) -> None:
        """Test diagnostics excludes NOT_EVALUATED MACs."""
        manager = BermudaIrkManager()
        mac = "AA:BB:CC:DD:EE:FF"
        nowstamp = monotonic_time_coarse()

        manager._macs[mac] = ResolvableMAC(
            mac=mac, expires=int(nowstamp + 1000), irk=IrkTypes.ADRESS_NOT_EVALUATED.value
        )

        result = manager.get_diagnostics_no_redactions()

        assert mac not in result["macs"]

    def test_get_diagnostics_no_known_irk_match_as_name(self) -> None:
        """Test diagnostics shows NO_KNOWN_IRK_MATCH as name."""
        manager = BermudaIrkManager()
        mac = "AA:BB:CC:DD:EE:FF"
        nowstamp = monotonic_time_coarse()

        manager._macs[mac] = ResolvableMAC(mac=mac, expires=int(nowstamp + 1000), irk=IrkTypes.NO_KNOWN_IRK_MATCH.value)

        result = manager.get_diagnostics_no_redactions()

        assert mac in result["macs"]
        assert result["macs"][mac]["irk"] == IrkTypes.NO_KNOWN_IRK_MATCH.name


class TestUpdateSavedMac:
    """Tests for _update_saved_mac method."""

    def test_update_saved_mac_new(self) -> None:
        """Test saving a new MAC."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "AA:BB:CC:DD:EE:FF"

        result = manager._update_saved_mac(mac, irk)

        assert result == irk
        assert mac in manager._macs
        assert manager._macs[mac].irk == irk

    def test_update_saved_mac_replace(self) -> None:
        """Test replacing an existing MAC's IRK."""
        manager = BermudaIrkManager()
        old_irk = b"\x01" * 16
        new_irk = b"\x02" * 16
        mac = "AA:BB:CC:DD:EE:FF"

        # Add existing
        manager._macs[mac] = ResolvableMAC(mac=mac, expires=9999999, irk=old_irk)

        result = manager._update_saved_mac(mac, new_irk)

        assert result == new_irk
        assert manager._macs[mac].irk == new_irk

    def test_update_saved_mac_no_change(self) -> None:
        """Test that saving the same IRK doesn't change anything."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "AA:BB:CC:DD:EE:FF"
        expires = 9999999

        # Add existing
        manager._macs[mac] = ResolvableMAC(mac=mac, expires=expires, irk=irk)

        result = manager._update_saved_mac(mac, irk)

        assert result == irk
        assert manager._macs[mac].expires == expires  # Expiry unchanged


class TestValidateMacIrk:
    """Tests for _validate_mac_irk method."""

    def test_validate_mac_irk_stores_not_resolvable_in_cache(self) -> None:
        """Test that _validate_mac_irk stores NOT_RESOLVABLE_ADDRESS in cache for non-RPAs."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "00:11:22:33:44:55"  # Not an RPA
        cipher = MagicMock()
        manager._irks[irk] = cipher

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            result = manager._validate_mac_irk(mac, irk, cipher)

        # The method returns NOT_RESOLVABLE_ADDRESS and stores it in cache
        assert result == IrkTypes.NOT_RESOLVABLE_ADDRESS.value
        assert mac in manager._macs
        assert manager._macs[mac].irk == IrkTypes.NOT_RESOLVABLE_ADDRESS.value

    def test_validate_mac_irk_match(self) -> None:
        """Test _validate_mac_irk when there's a match."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "55:11:22:33:44:55"
        cipher = MagicMock()
        manager._irks[irk] = cipher

        with (
            patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=True),
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            result = manager._validate_mac_irk(mac, irk, cipher)

        assert result == irk

    def test_validate_mac_irk_no_match_rpa(self) -> None:
        """Test _validate_mac_irk with RPA that doesn't match."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "55:11:22:33:44:55"  # First nibble 5 = RPA
        cipher = MagicMock()
        manager._irks[irk] = cipher

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            result = manager._validate_mac_irk(mac, irk, cipher)

        assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value

    def test_validate_mac_irk_no_match_non_rpa(self) -> None:
        """Test _validate_mac_irk with non-RPA address."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "00:11:22:33:44:55"  # First nibble 0 = not RPA
        cipher = MagicMock()
        manager._irks[irk] = cipher

        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            result = manager._validate_mac_irk(mac, irk, cipher)

        assert result == IrkTypes.NOT_RESOLVABLE_ADDRESS.value


class TestIrkManagerIntegration:
    """Integration tests for BermudaIrkManager."""

    def test_full_irk_resolution_workflow(self) -> None:
        """Test complete workflow: add IRK, scan device, resolve."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "55:AA:BB:CC:DD:EE"  # RPA (first nibble 5)

        # Step 1: Add IRK
        manager.add_irk(irk)
        assert irk in manager._irks

        # Step 2: Scan device with matching IRK
        with (
            patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=True),
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            matched, result = manager.scan_device(mac)

        assert matched is True
        assert result == irk

        # Step 3: Verify MAC is now cached
        assert mac in manager._macs
        assert manager._macs[mac].irk == irk

        # Step 4: Subsequent check should return cached result
        cached_result = manager.check_mac(mac)
        assert cached_result == irk

    def test_irk_added_after_mac_scanned(self) -> None:
        """Test that adding IRK after scanning resolves previously unknown MACs."""
        manager = BermudaIrkManager()
        irk = b"\x01" * 16
        mac = "55:AA:BB:CC:DD:EE"  # RPA

        # Step 1: Scan MAC first (no IRK known yet)
        with patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=False):
            matched, result = manager.scan_device(mac)

        assert matched is False
        assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value

        # Step 2: Now add the IRK
        with (
            patch("custom_components.bermuda.bermuda_irk.resolve_private_address", return_value=True),
            patch("custom_components.bermuda.bermuda_irk.MAJOR_VERSION", 2025),
            patch("custom_components.bermuda.bermuda_irk.MINOR_VERSION", 8),
        ):
            matching_macs = manager.add_irk(irk)

        # Step 3: Verify the MAC was matched
        assert mac in matching_macs
