"""
Regression tests: the orphan cleanup must only remove entities whose device is really gone.

On a live install every start removed (and then recreated) almost all Bermuda entities:

1. async_setup_entry() ran the cleanup right after the first refresh, which returns early
   while the Bluetooth ID data is loading, so coordinator.devices was empty or partial.
2. Even with a complete device list, base-address extraction failed for real unique_ids:
   iBeacon addresses contain underscores, scanner entities use the WiFi MAC, and
   "_training_floor" was cut as "_floor".
3. The duplicate check keyed entities by suffix only, so one device's per-scanner
   sensors (and the global sensors) counted as duplicates of each other.

Each removal fires an entity registry "remove" event; Proximity, for example, turns that
into a tracked_entity_removed repair.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.bermuda as bermuda_init
from custom_components.bermuda.const import DOMAIN

from .test_coordinator_methods import _make_coordinator

MAC = "aa:bb:cc:dd:ee:ff"
SCANNER_BLE = "88:57:21:b6:44:3a"
SCANNER_WIFI = "88:57:21:b6:44:38"
IBEACON = "50765cb7d9ea4e2199a4fa879613a492_12591_47910"


def _setup(hass: HomeAssistant, unique_ids: list[tuple[str, str]]):
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    coordinator = _make_coordinator(hass)
    coordinator.config_entry = entry
    registry = er.async_get(hass)
    entities = [
        registry.async_get_or_create(domain, DOMAIN, unique_id, config_entry=entry).entity_id
        for domain, unique_id in unique_ids
    ]
    removed: list[str] = []
    hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        lambda event: removed.append(event.data["entity_id"]) if event.data["action"] == "remove" else None,
    )
    return coordinator, entities, removed


async def _cleanup(hass: HomeAssistant, coordinator) -> None:
    await coordinator.async_cleanup_orphaned_entities()
    await hass.async_block_till_done()


async def test_cleanup_skipped_while_bt_ids_loading(hass: HomeAssistant) -> None:
    """A pending ID load means the device list was never built: nothing may be removed."""
    coordinator, _, removed = _setup(hass, [("device_tracker", MAC)])
    coordinator._get_or_create_device("11:22:33:44:55:66")
    coordinator._waitingfor_load_manufacturer_ids = True

    await _cleanup(hass, coordinator)

    assert removed == []


async def test_cleanup_skipped_with_empty_device_list(hass: HomeAssistant) -> None:
    """An empty device list is never evidence that every entity is orphaned."""
    coordinator, _, removed = _setup(hass, [("device_tracker", MAC)])

    await _cleanup(hass, coordinator)

    assert removed == []


async def test_cleanup_keeps_ibeacon_per_scanner_entities(hass: HomeAssistant) -> None:
    """iBeacon addresses contain underscores; their per-scanner and training entities stay."""
    coordinator, _, removed = _setup(
        hass,
        [
            ("sensor", f"{IBEACON}_{SCANNER_BLE}_range"),
            ("sensor", f"{IBEACON}_{SCANNER_BLE}_range_raw"),
            ("select", f"{IBEACON}_training_floor"),
            ("number", f"{IBEACON}_ref_power"),
            ("device_tracker", IBEACON),
        ],
    )
    coordinator._get_or_create_device(IBEACON)

    await _cleanup(hass, coordinator)

    assert removed == []


async def test_cleanup_keeps_scanner_entities_keyed_by_wifi_mac(hass: HomeAssistant) -> None:
    """Scanner entities use the WiFi MAC while the device is keyed by its BLE MAC."""
    coordinator, _, removed = _setup(hass, [("binary_sensor", f"{SCANNER_WIFI}_scanner_online")])
    coordinator._get_or_create_device(SCANNER_BLE).address_wifi_mac = SCANNER_WIFI

    await _cleanup(hass, coordinator)

    assert removed == []


async def test_cleanup_still_removes_real_orphans(hass: HomeAssistant) -> None:
    """Entities of addresses that no device has are still removed."""
    coordinator, entities, removed = _setup(hass, [("device_tracker", MAC), ("sensor", f"{MAC}_{SCANNER_BLE}_range")])
    coordinator._get_or_create_device("11:22:33:44:55:66")

    await _cleanup(hass, coordinator)

    assert sorted(removed) == sorted(entities)


async def test_setup_defers_cleanup(hass: HomeAssistant) -> None:
    """Setup schedules the cleanup for later instead of running it after the first refresh."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    with (
        patch("custom_components.bermuda.BermudaDataUpdateCoordinator") as coordinator_cls,
        patch("custom_components.bermuda.async_call_later", create=True) as call_later,
        patch.object(hass.config_entries, "async_forward_entry_setups"),
    ):
        coordinator = coordinator_cls.return_value
        coordinator.async_cleanup_device_registry_connections = _noop
        coordinator.async_refresh = _noop
        coordinator.last_update_success = True
        coordinator.async_cleanup_orphaned_entities = _fail
        await bermuda_init.async_setup_entry(hass, entry)

    call_later.assert_called_once()
    assert call_later.call_args.args[1] == getattr(bermuda_init, "ORPHAN_CLEANUP_DELAY", None)


async def _noop(*_args, **_kwargs) -> None:
    return None


async def _fail(*_args, **_kwargs) -> None:
    raise AssertionError("orphan cleanup must not run during setup")


async def test_cleanup_keeps_per_scanner_entities_of_one_device(hass: HomeAssistant) -> None:
    """One device has a distance sensor per scanner; they are not duplicates of each other."""
    other_scanner = "6c:c8:40:86:7b:a6"
    coordinator, _, removed = _setup(
        hass,
        [
            ("sensor", f"{MAC}_{SCANNER_BLE}_range"),
            ("sensor", f"{MAC}_{other_scanner}_range"),
            ("sensor", f"{MAC}_{SCANNER_BLE}_range_raw"),
            ("sensor", f"{MAC}_{other_scanner}_range_raw"),
        ],
    )
    coordinator._get_or_create_device(MAC)
    _attach_to_one_device(hass, coordinator)

    await _cleanup(hass, coordinator)

    assert removed == []


async def test_cleanup_keeps_global_entities(hass: HomeAssistant) -> None:
    """The global sensors share one device but are distinct entities."""
    coordinator, _, removed = _setup(
        hass,
        [
            ("sensor", "BERMUDA_GLOBAL_PROXY_COUNT"),
            ("sensor", "BERMUDA_GLOBAL_ACTIVE_PROXY_COUNT"),
            ("sensor", "BERMUDA_GLOBAL_DEVICE_COUNT"),
            ("sensor", "BERMUDA_GLOBAL_VISIBLE_DEVICE_COUNT"),
        ],
    )
    coordinator._get_or_create_device(MAC)
    _attach_to_one_device(hass, coordinator)

    await _cleanup(hass, coordinator)

    assert removed == []


async def test_cleanup_removes_old_address_format_duplicate(hass: HomeAssistant) -> None:
    """Same device under an old and a new address: the entity of the unknown address goes."""
    coordinator, entities, removed = _setup(hass, [("sensor", f"{MAC}_floor"), ("sensor", "fmdn:old-format-id_floor")])
    coordinator._get_or_create_device(MAC)
    _attach_to_one_device(hass, coordinator)

    with patch.object(coordinator, "_check_address_exists", return_value=True):
        await _cleanup(hass, coordinator)

    assert removed == [entities[1]]


def _attach_to_one_device(hass: HomeAssistant, coordinator) -> None:
    """Put all of the entry's entities on one HA device, as the platforms do."""
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=coordinator.config_entry.entry_id, identifiers={(DOMAIN, "shared")}
    )
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, coordinator.config_entry.entry_id):
        registry.async_update_entity(entity.entity_id, device_id=device.id)
