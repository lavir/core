"""Config flow for Hunter Douglas PowerView integration."""
from __future__ import annotations

import logging

from aiopvapi.helpers.aiorequest import AioRequest
import async_timeout
import voluptuous as vol

from homeassistant import config_entries, core, data_entry_flow, exceptions
from homeassistant.components.dhcp import HOSTNAME, IP_ADDRESS
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import async_get_device_info
from .const import DEVICE_NAME, DEVICE_SERIAL_NUMBER, DOMAIN, HUB_EXCEPTIONS

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})
HAP_SUFFIX = "._hap._tcp.local."


async def validate_input(hass: core.HomeAssistant, hub_address: str) -> dict[str, str]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """

    websession = async_get_clientsession(hass)

    pv_request = AioRequest(hub_address, loop=hass.loop, websession=websession)

    try:
        async with async_timeout.timeout(10):
            device_info = await async_get_device_info(pv_request)
    except HUB_EXCEPTIONS as err:
        raise CannotConnect from err

    # Return info that you want to store in the config entry.
    return {
        "title": device_info[DEVICE_NAME],
        "unique_id": device_info[DEVICE_SERIAL_NUMBER],
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hunter Douglas PowerView."""

    VERSION = 1

    def __init__(self):
        """Initialize the powerview config flow."""
        self.powerview_config = {}
        self.discovered_ip = None
        self.discovered_name = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            info, error = await self._async_validate_or_error(user_input[CONF_HOST])
            if not error:
                await self.async_set_unique_id(info["unique_id"])
                return self.async_create_entry(
                    title=info["title"], data={CONF_HOST: user_input[CONF_HOST]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def _async_validate_or_error(self, host):
        if self._host_already_configured(host):
            raise data_entry_flow.AbortFlow("already_configured")

        try:
            info = await validate_input(self.hass, host)
        except CannotConnect:
            return None, "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return None, "unknown"
        else:
            return info, None

    async def async_step_dhcp(self, discovery_info):
        """Handle DHCP discovery."""
        self.discovered_ip = discovery_info[IP_ADDRESS]
        self.discovered_name = discovery_info[HOSTNAME]
        return await self.async_step_discovery_confirm()

    async def async_step_homekit(self, discovery_info):
        """Handle HomeKit discovery."""
        self.discovered_ip = discovery_info[CONF_HOST]
        name = discovery_info[CONF_NAME]
        if name.endswith(HAP_SUFFIX):
            name = name[: -len(HAP_SUFFIX)]
        self.discovered_name = name
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self):
        """Confirm dhcp or homekit discovery."""
        # If we already have the host configured do
        # not open connections to it if we can avoid it.
        for progress in self._async_in_progress():
            if progress.get("context", {}).get(CONF_HOST) == self.discovered_ip:
                return self.async_abort(reason="already_in_progress")

        if self._host_already_configured(self.discovered_ip):
            return self.async_abort(reason="already_configured")

        info, error = await self._async_validate_or_error(self.discovered_ip)
        if error:
            return self.async_abort(reason=error)

        await self.async_set_unique_id(info["unique_id"], raise_on_progress=False)
        self._abort_if_unique_id_configured({CONF_HOST: self.discovered_ip})

        self.powerview_config = {
            CONF_HOST: self.discovered_ip,
            CONF_NAME: self.discovered_name,
        }
        return await self.async_step_link()

    async def async_step_link(self, user_input=None):
        """Attempt to link with Powerview."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.powerview_config[CONF_NAME],
                data={CONF_HOST: self.powerview_config[CONF_HOST]},
            )

        self.context[CONF_HOST] = self.discovered_ip
        self._set_confirm_only()
        return self.async_show_form(
            step_id="link", description_placeholders=self.powerview_config
        )

    def _host_already_configured(self, host):
        """See if we already have a hub with the host address configured."""
        existing_hosts = {
            entry.data.get(CONF_HOST)
            for entry in self._async_current_entries()
            if CONF_HOST in entry.data
        }
        return host in existing_hosts


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""
