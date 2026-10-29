"""Test Bermuda services module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.json import json_bytes

from custom_components.bermuda.area_selection import AreaTests
from custom_components.bermuda.const import ADDR_TYPE_PRIVATE_BLE_DEVICE, CONF_DEVICES, DOMAIN
from custom_components.bermuda.services import (
    DUMP_DEVICE_SOFT_LIMIT,
    BermudaServiceHandler,
)


class TestBermudaServiceHandler:
    """Tests for BermudaServiceHandler class."""

    def _create_handler(
        self,
        scanner_list: list[str] | None = None,
        devices: dict | None = None,
        options: dict | None = None,
        pb_state_sources: dict | None = None,
    ) -> BermudaServiceHandler:
        """Create a BermudaServiceHandler instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.scanner_list = scanner_list or []
        mock_coordinator.devices = devices or {}
        mock_coordinator.options = options or {}
        mock_coordinator.pb_state_sources = pb_state_sources or {}
        mock_coordinator.count_active_devices = MagicMock(return_value=5)
        mock_coordinator.count_active_scanners = MagicMock(return_value=3)

        return BermudaServiceHandler(mock_coordinator)

    def test_init(self) -> None:
        """Test that handler initializes correctly."""
        handler = self._create_handler()

        assert handler.redactions == {}
        assert handler.stamp_redactions_expiry is None

    def test_redaction_list_update_scanners(self) -> None:
        """Test that redaction_list_update adds scanners."""
        handler = self._create_handler(scanner_list=["AA:BB:CC:DD:EE:FF"])

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        # Should have redactions for different MAC formats
        assert "aa:bb:cc:dd:ee:ff" in handler.redactions
        # Should update expiry stamp
        assert handler.stamp_redactions_expiry is not None

    def test_redaction_list_update_configured_devices_mac(self) -> None:
        """Test that redaction_list_update adds configured MAC devices."""
        handler = self._create_handler(options={CONF_DEVICES: ["11:22:33:44:55:66"]})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert "11:22:33:44:55:66" in handler.redactions

    def test_redaction_list_update_configured_devices_ibeacon(self) -> None:
        """Test that redaction_list_update adds configured iBeacon devices."""
        ibeacon_addr = "12345678-1234-1234-1234-123456789012_1_2"
        handler = self._create_handler(options={CONF_DEVICES: [ibeacon_addr]})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert ibeacon_addr in handler.redactions

    def test_redaction_list_update_other_devices(self) -> None:
        """Test that redaction_list_update adds other devices."""
        mock_device = MagicMock()
        mock_device.address_type = "public"

        handler = self._create_handler(devices={"aa:bb:cc:dd:ee:01": mock_device})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert "aa:bb:cc:dd:ee:01" in handler.redactions

    def test_redaction_list_update_private_ble_device(self) -> None:
        """Test that redaction_list_update handles Private BLE devices."""
        mock_device = MagicMock()
        mock_device.address_type = ADDR_TYPE_PRIVATE_BLE_DEVICE

        handler = self._create_handler(devices={"irk_address": mock_device})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert "irk_address" in handler.redactions
        assert "IRK_DEV" in handler.redactions["irk_address"]

    def test_redact_data_string_full_match(self) -> None:
        """Test that redact_data handles full string match."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED_MAC"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data("AA:BB:CC:DD:EE:FF")

        assert result == "REDACTED_MAC"

    def test_redact_data_string_partial_match(self) -> None:
        """Test that redact_data handles partial string match."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data("Device: aa:bb:cc:dd:ee:ff found")

        assert "REDACTED" in result

    def test_redact_data_dict(self) -> None:
        """Test that redact_data handles dicts."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED_MAC"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data({"key": "aa:bb:cc:dd:ee:ff", "other": "value"})

        assert result["key"] == "REDACTED_MAC"
        assert result["other"] == "value"

    def test_redact_data_list(self) -> None:
        """Test that redact_data handles lists."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data(["aa:bb:cc:dd:ee:ff", "other"])

        assert result[0] == "REDACTED"
        assert result[1] == "other"

    def test_redact_data_other_types(self) -> None:
        """Test that redact_data passes through other types."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            assert handler.redact_data(42) == 42
            assert handler.redact_data(None) is None
            assert handler.redact_data(3.14) == 3.14

    def test_redact_data_dataclass(self) -> None:
        """MAC addresses inside a dataclass value (such as AreaTests in dump_devices) are redacted."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0
        tests = AreaTests(
            scannername=("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"), offline_scanner_addrs="aa:bb:cc:dd:ee:ff"
        )

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data({"area_tests": tests})

        rendered = json_bytes(result).decode()
        assert "aa:bb:cc:dd:ee:ff" not in rendered
        assert "11:22:33:44:55:66" not in rendered
        assert result["area_tests"]["decision_path"] == "UNKNOWN"
        assert tests.scannername == ("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66")

    def test_redact_data_tuple(self) -> None:
        """MAC addresses inside a tuple are redacted."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data(("aa:bb:cc:dd:ee:ff", "other"))

        assert "aa:bb:cc:dd:ee:ff" not in json_bytes(result).decode()
        assert result[1] == "other"

    def test_redact_data_time_values_pass_through(self) -> None:
        """Dates, times and durations keep their type: the encoder formats them itself."""
        import datetime  # noqa: PLC0415

        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0
        values = (datetime.datetime(2026, 10, 28, 12, 0), datetime.time(12, 0), datetime.timedelta(seconds=5))

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            for value in values:
                assert handler.redact_data(value) is value

    def test_redact_data_object_with_as_dict(self) -> None:
        """An object with as_dict() is redacted as that mapping, like the encoder would render it."""

        class WithAsDict:
            def as_dict(self) -> dict:
                return {"address": "aa:bb:cc:dd:ee:ff", "name": "n"}

        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data({"x": WithAsDict()})

        assert result["x"]["name"] == "n"
        assert "aa:bb:cc:dd:ee:ff" not in json.dumps(result)

    def test_redact_data_object_as_repr(self) -> None:
        """Any other object becomes the encoder's repr fallback, redacted; a non-mapping as_dict() counts as none."""

        class Plain:
            def __repr__(self) -> str:
                return "Plain(aa:bb:cc:dd:ee:ff)"

        class OddAsDict(Plain):
            def as_dict(self) -> str:
                return "aa:bb:cc:dd:ee:ff"

        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            for obj in (Plain(), OddAsDict()):
                result = handler.redact_data(obj)
                assert set(result) == {"__type", "repr"}
                assert result["repr"].startswith("Plain(")
                assert "aa:bb:cc:dd:ee:ff" not in result["repr"]

    def test_redact_data_object_as_dict_unusable(self) -> None:
        """A class object or an as_dict() that raises falls back to the redacted repr instead of aborting."""

        class WithAsDict:
            def as_dict(self) -> dict:
                return {"address": "aa:bb:cc:dd:ee:ff"}

            def __repr__(self) -> str:
                return "WithAsDict(aa:bb:cc:dd:ee:ff)"

        class Broken(WithAsDict):
            def as_dict(self) -> dict:
                msg = "aa:bb:cc:dd:ee:ff"
                raise TypeError(msg)

        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            for obj in (WithAsDict, Broken()):
                result = handler.redact_data({"x": obj})["x"]
                assert set(result) == {"__type", "repr"}
                assert "aa:bb:cc:dd:ee:ff" not in result["repr"]

    def test_redact_data_bytes_as_redacted_hex(self) -> None:
        """Bytes (manufacturer and service data) become hex with known ids redacted, not an escaped repr."""
        uuid = "fda50693a4e24fb1afcfc6eb07647825"
        beacon = MagicMock()
        beacon.address_type = "ibeacon"
        handler = self._create_handler(devices={f"{uuid}_1_2": beacon})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0
        advert = bytes.fromhex("0215" + uuid + "00010002c5")

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            for value in (advert, bytearray(advert), memoryview(advert)):
                result = handler.redact_data({"manufacturer_data": {76: value}})["manufacturer_data"][76]
                assert isinstance(result, str)
                assert uuid not in result.lower()
                assert result.startswith("0215")
            unknown = handler.redact_data(bytes.fromhex("0a0b0c"))

        assert unknown == "0a0b0c"

    def test_redact_data_reversed_mac_in_advert_bytes(self) -> None:
        """R2-F1: a MAC in reversed byte order (Xiaomi fe95, pvvx 181a service data) is redacted."""
        device = MagicMock()
        device.address_type = "public"
        handler = self._create_handler(devices={"a4:c1:38:12:34:56": device})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0
        reversed_hex = "563412" + "38c1a4"
        advert = bytes.fromhex("3050" + reversed_hex + "0d")

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data({"service_data": {"fe95": advert}})["service_data"]["fe95"]
            text = handler.redact_data(f"mac_le={reversed_hex}")

        assert reversed_hex not in result
        assert result.startswith("3050")
        assert result.endswith("0d")
        assert reversed_hex not in text
        assert handler.redactions[reversed_hex] == handler.redactions["a4:c1:38:12:34:56"]

    def test_redact_data_beacon_uuid_with_hyphens(self) -> None:
        """R2-F6: the beacon UUID is redacted in its hyphenated text form and as uuid.UUID."""
        import uuid as uuid_mod  # noqa: PLC0415

        plain = "fda50693a4e24fb1afcfc6eb07647825"
        hyphen = "fda50693-a4e2-4fb1-afcf-c6eb07647825"
        beacon = MagicMock()
        beacon.address_type = "ibeacon"
        handler = self._create_handler(devices={f"{plain}_1_2": beacon})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            text = handler.redact_data(f"uuid {hyphen.upper()} seen")
            obj = handler.redact_data({"u": uuid_mod.UUID(hyphen)})["u"]

        assert hyphen not in text.lower()
        assert hyphen not in obj["repr"].lower()
        assert handler.redactions[hyphen] == handler.redactions[plain]

    def test_redact_data_object_whose_attributes_fail(self, caplog: pytest.LogCaptureFixture) -> None:
        """R2-F2/R2-F3: a raising as_dict lookup, a raising repr and a released memoryview do not abort."""

        class RaisingLookup:
            @property
            def as_dict(self) -> None:
                msg = "aa:bb:cc:dd:ee:ff"
                raise ValueError(msg)

            def __repr__(self) -> str:
                return "RaisingLookup(aa:bb:cc:dd:ee:ff)"

        class RaisingRepr:
            def __repr__(self) -> str:
                msg = "aa:bb:cc:dd:ee:ff"
                raise RuntimeError(msg)

        released = memoryview(b"\x01\x02")
        released.release()
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with (
            caplog.at_level("DEBUG", logger="custom_components.bermuda"),
            patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0),
        ):
            result = handler.redact_data({"a": RaisingLookup(), "b": RaisingRepr(), "c": released})

        assert set(result["a"]) == {"__type", "repr"}
        assert "aa:bb:cc:dd:ee:ff" not in result["a"]["repr"]
        assert set(result["b"]) == {"__type", "repr"}
        assert "aa:bb:cc:dd:ee:ff" not in result["b"]["repr"]
        assert set(result["c"]) == {"__type", "repr"}
        assert "RaisingLookup" in caplog.text
        assert "RaisingRepr" in caplog.text
        # The exception text may carry an identifier, so it is never logged.
        assert "aa:bb:cc:dd:ee:ff" not in caplog.text

    def test_redact_data_object_failing_type_check(self, caplog: pytest.LogCaptureFixture) -> None:
        """R3-F2: an object that fails already at isinstance is replaced; the dump and its neighbours go on."""

        class RaisingGetattribute:
            def __getattribute__(self, name: str) -> object:
                msg = f"no {name} for aa:bb:cc:dd:ee:ff"
                raise AttributeError(msg)

        class RaisingClass:
            @property  # type: ignore[misc]
            def __class__(self) -> type:
                msg = "aa:bb:cc:dd:ee:ff"
                raise RuntimeError(msg)

        device = MagicMock()
        device.address_type = "public"
        handler = self._create_handler(devices={"aa:bb:cc:dd:ee:ff": device})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0

        with (
            caplog.at_level("DEBUG", logger="custom_components.bermuda"),
            patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0),
        ):
            result = handler.redact_data(
                {"a": RaisingGetattribute(), "b": [RaisingClass()], "c": "seen aa:bb:cc:dd:ee:ff"}
            )

        assert result["a"] == {"__type": "<unavailable>", "repr": "<redaction failed: AttributeError>"}
        assert result["b"] == [{"__type": "<unavailable>", "repr": "<redaction failed: RuntimeError>"}]
        assert "aa:bb:cc:dd:ee:ff" not in result["c"]
        assert "redaction failed (AttributeError)" in caplog.text
        assert "redaction failed (RuntimeError)" in caplog.text
        # The exception text may carry an identifier, so it is never logged.
        assert "aa:bb:cc:dd:ee:ff" not in caplog.text

    def test_redact_data_failing_key_and_set_member_keep_their_neighbours(self) -> None:
        """R5-F2/R5-F4: a raising key or set member becomes a placeholder text; its mapping or set stays."""

        class RaisingClass:
            def __init__(self, error: type[Exception]) -> None:
                self.error = error

            def __hash__(self) -> int:
                return 1

            @property  # type: ignore[misc]
            def __class__(self) -> type:
                raise object.__getattribute__(self, "error")("aa:bb:cc:dd:ee:ff")

        device = MagicMock()
        device.address_type = "public"
        handler = self._create_handler(devices={"aa:bb:cc:dd:ee:ff": device})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data(
                {"ok": "aa:bb:cc:dd:ee:ff", RaisingClass(ValueError): 1, "s": {"x", RaisingClass(KeyError)}}
            )

        placeholder = "<redaction failed: {}>"
        assert "aa:bb:cc:dd:ee:ff" not in result["ok"]
        (key,) = (k for k in result if k not in {"ok", "s"})
        assert placeholder.format("ValueError") in key
        assert result[key] == 1
        assert isinstance(result["s"], set)
        assert "x" in result["s"]
        assert any(placeholder.format("KeyError") in member for member in result["s"])
        assert "aa:bb:cc:dd:ee:ff" not in repr(result)

    def test_redact_data_does_not_swallow_base_exceptions(self) -> None:
        """R5-F4: only Exception becomes a placeholder; a BaseException such as a cancellation passes."""

        class Stop(BaseException):
            pass

        class RaisingClass:
            @property  # type: ignore[misc]
            def __class__(self) -> type:
                raise Stop

        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0
        with (
            patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0),
            pytest.raises(Stop),
        ):
            handler.redact_data({"a": RaisingClass()})

    def test_redact_data_advert_bytes_scanned_per_byte(self) -> None:
        """R2-F5: inside advert bytes a key is only matched on a byte boundary, not between nibbles."""
        device = MagicMock()
        device.address_type = "public"
        handler = self._create_handler(devices={"a4:c1:38:12:34:56": device})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0
        aligned = bytes.fromhex("01a4c138123456ff")
        shifted = bytes.fromhex("0a4c138123456fff")  # same digits, shifted by one nibble

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            out = handler.redact_data([aligned, shifted])

        assert "a4c138123456" not in out[0]
        assert out[1] == shifted.hex()

    def test_redact_data_advert_bytes_odd_key_keeps_byte_grid(self) -> None:
        """R2-F5: a key of odd length cannot be whole bytes; matching it would shift the grid and miss a MAC."""
        mac_dev = MagicMock()
        mac_dev.address_type = "public"
        odd_dev = MagicMock()
        odd_dev.address_type = "public"
        handler = self._create_handler(devices={"a4:c1:38:12:34:56": mac_dev, "abcde": odd_dev})
        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()
        handler.stamp_redactions_expiry = 200.0
        advert = bytes.fromhex("abcde0" + "a4c138123456")

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            out = handler.redact_data(advert)

        assert "a4c138123456" not in out
        assert out.startswith("abcde0")

    def test_mac_and_uuid_keys_fallbacks_and_cache(self) -> None:
        """R3-F1/R3-F6/R3-F7: the cached MAC formats stay untouched; non-MACs and non-UUIDs keep one key."""
        from custom_components.bermuda.services import _mac_keys, _uuid_keys  # noqa: PLC0415
        from custom_components.bermuda.util import mac_explode_formats  # noqa: PLC0415

        # R4-F1: an earlier test may already have cached this MAC; start from an empty cache
        # so that a shared (mutated) result is seen here and not hidden by an older entry.
        mac_explode_formats.cache_clear()
        mac = "a4:c1:38:12:34:56"
        forward = {"a4:c1:38:12:34:56", "a4c138123456", "a4-c1-38-12-34-56", "a4_c1_38_12_34_56", "a4.c1.38.12.34.56"}
        assert mac_explode_formats(mac) == forward
        assert _mac_keys(mac) == forward | {"56341238c1a4"}
        assert mac_explode_formats(mac) == forward
        # R4-F2: against the literal, not against the function the fallback delegates to.
        assert _mac_keys("not-a-mac-17-char") == {"not-a-mac-17-char"}
        assert _uuid_keys("1234") == {"1234"}
        assert _uuid_keys("fda50693.a4e24fb1afcfc6eb0764782") == {"fda50693.a4e24fb1afcfc6eb0764782"}

    def test_redaction_list_reads_fmdn_ids_from_devices(self) -> None:
        """FMDN ids of a device enter the list without the manager; non-string attributes are ignored."""
        from custom_components.bermuda.const import ADDR_TYPE_FMDN_DEVICE  # noqa: PLC0415

        device = MagicMock()
        device.address_type = ADDR_TYPE_FMDN_DEVICE
        device.fmdn_device_id = "DevQuiet0815"
        device.fmdn_canonical_id = None
        unset = MagicMock()  # attributes never set: MagicMock children, not strings
        unset.address_type = ADDR_TYPE_FMDN_DEVICE
        other = MagicMock()
        other.address_type = "public"
        other.fmdn_device_id = "devnotfmdn99"
        handler = self._create_handler(devices={"fmdn:k": device, "fmdn:u": unset, "aa:bb:cc:dd:ee:01": other})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert handler.redactions["devquiet0815"].startswith("FMDN_DEV_")
        assert all(isinstance(k, str) for k in handler.redactions)
        assert "devnotfmdn99" not in handler.redactions

    def test_redact_data_generic_mac_redaction(self) -> None:
        """Test that redact_data handles generic MAC addresses."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            # A MAC that's not in the redaction list should still be redacted generically
            result = handler.redact_data("unknown: 99:88:77:66:55:44")

        # Generic redaction keeps first and last octets
        assert "99" in result
        assert "44" in result
        assert "xx" in result

    def test_redact_data_two_hits_in_one_string(self) -> None:
        """T-R3: two known addresses in one string are both replaced."""
        handler = self._create_handler(scanner_list=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        result = handler.redact_data("aa:bb:cc:dd:ee:01__aa:bb:cc:dd:ee:02")

        assert result.count("::SCANNER_") == 2
        assert "xx:xx" not in result

    def test_redact_other_no_raw_id(self) -> None:
        """T-R13: placeholders for non-MAC ids carry no raw id and distinct numbers."""
        seen = MagicMock()
        seen.address_type = "fmdn"
        handler = self._create_handler(
            options={CONF_DEVICES: ["fmdn:cfgonealpha", "fmdn:cfgtwobravo"]},
            devices={"fmdn:seencharlie": seen},
        )

        handler.redaction_list_update()

        values = [handler.redactions[k] for k in ("fmdn:cfgonealpha", "fmdn:cfgtwobravo", "fmdn:seencharlie")]
        for raw, value in zip(("cfgonealpha", "cfgtwobravo", "seencharlie"), values, strict=True):
            assert raw not in value.lower()
        assert len(set(values)) == 3

    @pytest.mark.asyncio
    async def test_async_dump_devices_all(self) -> None:
        """Test async_dump_devices returns all devices."""
        mock_device = MagicMock()
        mock_device.to_dict = MagicMock(return_value={"name": "Test Device"})

        handler = self._create_handler(
            devices={"aa:bb:cc:dd:ee:ff": mock_device},
        )

        mock_call = MagicMock()
        mock_call.data = {}

        result = await handler.async_dump_devices(mock_call)

        assert "aa:bb:cc:dd:ee:ff" in result
        assert result["aa:bb:cc:dd:ee:ff"]["name"] == "Test Device"

    @pytest.mark.asyncio
    async def test_async_dump_devices_specific_address(self) -> None:
        """Test async_dump_devices with specific address filter."""
        mock_device1 = MagicMock()
        mock_device1.to_dict = MagicMock(return_value={"name": "Device 1"})
        mock_device2 = MagicMock()
        mock_device2.to_dict = MagicMock(return_value={"name": "Device 2"})

        handler = self._create_handler(
            devices={
                "aa:bb:cc:dd:ee:01": mock_device1,
                "aa:bb:cc:dd:ee:02": mock_device2,
            },
        )

        mock_call = MagicMock()
        mock_call.data = {"addresses": "AA:BB:CC:DD:EE:01"}

        result = await handler.async_dump_devices(mock_call)

        assert "aa:bb:cc:dd:ee:01" in result
        assert "aa:bb:cc:dd:ee:02" not in result

    @pytest.mark.asyncio
    async def test_async_dump_devices_configured_only(self) -> None:
        """Test async_dump_devices with configured_devices filter."""
        mock_device = MagicMock()
        mock_device.to_dict = MagicMock(return_value={"name": "Test Device"})

        handler = self._create_handler(
            scanner_list=["scanner:address"],
            devices={"scanner:address": mock_device},
            options={CONF_DEVICES: []},
        )

        mock_call = MagicMock()
        mock_call.data = {"configured_devices": True}

        result = await handler.async_dump_devices(mock_call)

        assert "scanner:address" in result

    @pytest.mark.asyncio
    async def test_async_dump_devices_with_redaction(self, hass: HomeAssistant) -> None:
        """Test async_dump_devices with redaction enabled (redaction runs in the executor)."""
        mock_device = MagicMock()
        mock_device.to_dict = MagicMock(return_value={"mac": "aa:bb:cc:dd:ee:ff"})

        handler = self._create_handler(
            devices={"aa:bb:cc:dd:ee:ff": mock_device},
        )
        handler.coordinator.hass = hass

        mock_call = MagicMock()
        mock_call.data = {"redact": True}

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = await handler.async_dump_devices(mock_call)

        # MAC should be redacted in the result
        assert "aa:bb:cc:dd:ee:ff" not in str(result)

    @pytest.mark.asyncio
    async def test_async_dump_devices_soft_limit(self) -> None:
        """Test async_dump_devices applies soft limit for large device counts."""
        # Create many mock devices
        devices = {}
        for i in range(DUMP_DEVICE_SOFT_LIMIT + 100):
            mock_device = MagicMock()
            mock_device.to_dict = MagicMock(return_value={"name": f"Device {i}"})
            devices[f"aa:bb:cc:dd:{i:02d}:{i:02d}"] = mock_device

        handler = self._create_handler(
            devices=devices,
            scanner_list=["scanner:01"],
            options={CONF_DEVICES: ["cfg:device"]},
        )

        mock_call = MagicMock()
        mock_call.data = {}

        result = await handler.async_dump_devices(mock_call)

        # Should have summary indicating limit was applied
        assert "summary" in result
        assert result["summary"]["limited"] is True


class TestServicesIntegration:
    """Integration tests for services module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import services

        assert hasattr(services, "BermudaServiceHandler")
        assert hasattr(services, "DUMP_DEVICE_SOFT_LIMIT")

    def test_dump_device_soft_limit_is_reasonable(self) -> None:
        """Test that DUMP_DEVICE_SOFT_LIMIT is a reasonable value."""
        assert DUMP_DEVICE_SOFT_LIMIT > 100
        assert DUMP_DEVICE_SOFT_LIMIT < 10000


class TestResetRoomAutoLearningService:
    """Tests for the reset_room_auto_learning service handler."""

    @pytest.mark.asyncio
    async def test_service_passes_areas_and_returns_counts(self) -> None:
        """The handler passes the area ids to the coordinator and returns its counts."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_reset_room_auto_learning = AsyncMock(return_value={"buero": 7})
        handler = BermudaServiceHandler(mock_coordinator)
        mock_call = MagicMock()
        mock_call.data = {"area_id": ["buero"]}

        result = await handler.async_reset_room_auto_learning(mock_call)

        mock_coordinator.async_reset_room_auto_learning.assert_awaited_once_with(["buero"])
        assert result == {"buero": 7}


def test_every_translation_describes_every_service() -> None:
    """Each translation names and describes every declared service and each of its fields."""
    base = Path(__file__).parent.parent / "custom_components" / "bermuda"
    declared = yaml.safe_load((base / "services.yaml").read_text(encoding="utf-8"))
    for translation in sorted((base / "translations").glob("*.json")):
        services = json.loads(translation.read_text(encoding="utf-8")).get("services", {})
        assert set(services) == set(declared), translation.name
        for name, spec in declared.items():
            entry = services[name]
            assert _text(entry, "name") and _text(entry, "description"), (translation.name, name)
            fields = entry.get("fields", {})
            assert set(fields) == set(spec.get("fields", {})), (translation.name, name)
            for field_name, field in fields.items():
                assert _text(field, "name") and _text(field, "description"), (translation.name, name, field_name)


def test_every_raised_translation_key_has_an_english_message() -> None:
    """Each translation_key raised in services.py has a non-empty English message."""
    base = Path(__file__).parent.parent / "custom_components" / "bermuda"
    keys = set(re.findall(r'translation_key="([a-z_]+)"', (base / "services.py").read_text(encoding="utf-8")))
    assert keys, "no translation_key found in services.py"
    exceptions = json.loads((base / "translations" / "en.json").read_text(encoding="utf-8")).get("exceptions", {})
    for key in keys:
        assert _text(exceptions.get(key, {}), "message"), key


def _text(entry: dict[str, object], key: str) -> bool:
    """Return whether entry[key] is a string with visible text."""
    value = entry.get(key)
    return isinstance(value, str) and bool(value.strip())


async def _async_setup(hass: HomeAssistant) -> bool:
    """Run the integration's async_setup, imported here so the module collects without it."""
    from custom_components.bermuda import async_setup  # noqa: PLC0415

    return await async_setup(hass, {})


class TestServicesRegisteredInAsyncSetup:
    """The services exist without a loaded entry and route to the loaded one."""

    @pytest.mark.asyncio
    async def test_services_registered_without_entry(self, hass: HomeAssistant) -> None:
        """async_setup registers both services before any config entry is set up."""
        assert await _async_setup(hass) is True

        assert hass.services.has_service(DOMAIN, "dump_devices")
        assert hass.services.has_service(DOMAIN, "reset_room_auto_learning")

    @pytest.mark.asyncio
    async def test_service_without_loaded_entry_raises_validation_error(self, hass: HomeAssistant) -> None:
        """Without a loaded entry the service fails with a validation error."""
        await _async_setup(hass)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "reset_room_auto_learning", {"area_id": ["buero"]}, blocking=True, return_response=True
            )

    @pytest.mark.asyncio
    async def test_dump_devices_routes_to_loaded_entry(self, hass: HomeAssistant) -> None:
        """dump_devices reaches the loaded entry's coordinator and keeps its schema."""
        await _async_setup(hass)
        coordinator = MagicMock(service_dump_devices=AsyncMock(return_value={"aa": {"name": "x"}}))
        entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))

        with patch.object(hass.config_entries, "async_loaded_entries", return_value=[entry]):
            response = await hass.services.async_call(
                DOMAIN, "dump_devices", {"redact": True}, blocking=True, return_response=True
            )
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN, "dump_devices", {"unknown": 1}, blocking=True, return_response=True
                )

        assert response == {"aa": {"name": "x"}}
        assert coordinator.service_dump_devices.await_args.args[0].data == {"redact": True}

    @pytest.mark.asyncio
    async def test_validation_error_uses_translated_key(self, hass: HomeAssistant) -> None:
        """The validation error without a loaded entry carries the integration's translation key."""
        await _async_setup(hass)

        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "dump_devices", {}, blocking=True, return_response=True)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "integration_not_loaded"

    @pytest.mark.asyncio
    async def test_service_routes_to_loaded_entry(self, hass: HomeAssistant) -> None:
        """With a loaded entry the call reaches its coordinator and returns its response."""
        await _async_setup(hass)
        coordinator = MagicMock(service_reset_room_auto_learning=AsyncMock(return_value={"buero": 2}))
        entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))

        with patch.object(hass.config_entries, "async_loaded_entries", return_value=[entry]):
            response = await hass.services.async_call(
                DOMAIN, "reset_room_auto_learning", {"area_id": "buero"}, blocking=True, return_response=True
            )

        assert response == {"buero": 2}
        call = coordinator.service_reset_room_auto_learning.await_args.args[0]
        assert call.data["area_id"] == ["buero"]
