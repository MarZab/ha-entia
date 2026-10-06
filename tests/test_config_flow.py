"""Config flow tests: the login is checked against the fake Entia server."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia.const import DOMAIN

from .fake_ape import FakeApe
from .messages import startup_responses

USER_INPUT = {CONF_USERNAME: "Test-Entia-ID", CONF_PASSWORD: "test-pin"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Iterator[AsyncMock]:
    """Keep created and reloaded entries from setting up."""
    with patch("custom_components.entia.async_setup_entry", return_value=True) as mock:
        yield mock


def reject_login(ape: FakeApe) -> None:
    ape.reply("CONNECT", startup_responses()["CONNECT"])
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])


async def start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_creates_entry(hass: HomeAssistant, ape: FakeApe) -> None:
    ape.load("synthetic_startup.json")
    flow_id = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test-Entia-ID"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "test-entia-id"
    assert ape.commands()[:2] == ["CONNECT", "FLAT_CONNECT"]


async def test_user_flow_rejects_bad_credentials(hass: HomeAssistant, ape: FakeApe) -> None:
    reject_login(ape)
    flow_id = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    ape.load("synthetic_startup.json")
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant, ape: FakeApe) -> None:
    ape.down = True
    flow_id = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unexpected_error(hass: HomeAssistant, ape: FakeApe) -> None:
    flow_id = await start_user_flow(hass)

    with patch("custom_components.entia.api.session.Session.wait_ready", side_effect=ValueError("boom")):
        result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["errors"] == {"base": "unknown"}
    ape.reset()


async def test_user_flow_aborts_when_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="test-entia-id", data=USER_INPUT).add_to_hass(hass)
    flow_id = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, ape: FakeApe) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="test-entia-id", data=USER_INPUT)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    reject_login(ape)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    ape.load("synthetic_startup.json")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new-pin"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_USERNAME: "Test-Entia-ID", CONF_PASSWORD: "new-pin"}
    assert ape.sent("FLAT_CONNECT")[-1]["params"]["password"] == "new-pin"
