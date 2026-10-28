"""Both Bermuda services are admin services.

``reset_room_auto_learning`` deletes learned data, and ``dump_devices`` exposes
every address (and, until RC1.3, could freeze Home Assistant through its
redaction). Both run for admins and for calls without a user (automations); a
script started by a user runs with that user's rights. Any other user is
refused before the coordinator is reached.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized, UnknownUser

from custom_components.bermuda.const import DOMAIN

SERVICES: dict[str, tuple[str, dict[str, Any], Any]] = {
    "reset_room_auto_learning": ("service_reset_room_auto_learning", {"area_id": ["raum_a"]}, {"raum_a": 2}),
    "dump_devices": ("service_dump_devices", {"redact": True}, {"aa": {"name": "x"}}),
}


async def _setup_with_entry(hass: HomeAssistant, method: str, response: Any) -> MagicMock:
    """Register the services and route them to a coordinator mock."""
    from custom_components.bermuda import async_setup  # noqa: PLC0415

    assert await async_setup(hass, {})
    coordinator = MagicMock()
    setattr(coordinator, method, AsyncMock(return_value=response))
    return coordinator


async def _call(hass: HomeAssistant, coordinator: MagicMock, service: str, data: dict, context: Context) -> Any:
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    with patch.object(hass.config_entries, "async_loaded_entries", return_value=[entry]):
        return await hass.services.async_call(
            DOMAIN, service, data, blocking=True, return_response=True, context=context
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("service", sorted(SERVICES))
async def test_non_admin_is_refused(hass: HomeAssistant, hass_read_only_user: User, service: str) -> None:
    """A user without admin rights gets Unauthorized and the coordinator is not called."""
    method, data, response = SERVICES[service]
    coordinator = await _setup_with_entry(hass, method, response)

    with pytest.raises(Unauthorized):
        await _call(hass, coordinator, service, data, Context(user_id=hass_read_only_user.id))

    getattr(coordinator, method).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", sorted(SERVICES))
async def test_admin_is_served(hass: HomeAssistant, hass_admin_user: User, service: str) -> None:
    """An admin gets the response as before."""
    method, data, response = SERVICES[service]
    coordinator = await _setup_with_entry(hass, method, response)

    result = await _call(hass, coordinator, service, data, Context(user_id=hass_admin_user.id))

    assert result == response


@pytest.mark.asyncio
@pytest.mark.parametrize("service", sorted(SERVICES))
async def test_automation_without_user_is_served(hass: HomeAssistant, service: str) -> None:
    """A call without a user (an automation) is served."""
    method, data, response = SERVICES[service]
    coordinator = await _setup_with_entry(hass, method, response)

    result = await _call(hass, coordinator, service, data, Context())

    assert result == response


@pytest.mark.asyncio
@pytest.mark.parametrize("service", sorted(SERVICES))
async def test_unknown_user_is_refused(hass: HomeAssistant, service: str) -> None:
    """A user id that does not exist gets UnknownUser."""
    method, data, response = SERVICES[service]
    coordinator = await _setup_with_entry(hass, method, response)

    with pytest.raises(UnknownUser):
        await _call(hass, coordinator, service, data, Context(user_id="no-such-user"))

    getattr(coordinator, method).assert_not_awaited()
