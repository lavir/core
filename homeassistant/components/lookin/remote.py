"""The lookin integration remote platform."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.remote import ATTR_DELAY_SECS, RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aiolookin import IRFormat
from .const import DOMAIN
from .entity import LookinDeviceEntity
from .models import LookinData

KNOWN_FORMATS = {format.value: format for format in IRFormat}

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light platform for lookin from a config entry."""
    lookin_data: LookinData = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([LookinRemoteEntity(lookin_data)])


class LookinRemoteEntity(LookinDeviceEntity, RemoteEntity):
    """Representation of a lookin remote."""

    def __init__(self, lookin_data: LookinData) -> None:
        """Initialize the remote."""
        super().__init__(lookin_data)
        self._attr_name = f"{self._lookin_device.name} Remote"
        self._attr_unique_id = self._lookin_device.id
        self._attr_is_on = True
        self._attr_supported_features = 0

    async def async_turn_on(self, **kwargs):
        """Turn on the remote."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn off the remote."""
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_send_command(self, command, **kwargs):
        """Send a list of commands."""
        delay = kwargs[ATTR_DELAY_SECS]

        if not self._attr_is_on:
            _LOGGER.warning(
                "send_command canceled: %s entity is turned off", self.entity_id
            )
            return

        for idx, codes in enumerate(command):
            if idx:
                await asyncio.sleep(delay)

            format = None
            for known_format, ir_format in KNOWN_FORMATS:
                prefix = f"{known_format}:"
                if codes.startswith(prefix):
                    codes = codes[(len(prefix)) :]
                    format = ir_format
                    break

            if not format:
                prefixes = [f"{known_format}:" for known_format in KNOWN_FORMATS]
                raise HomeAssistantError(
                    f"Commands must be prefixed with one of {prefixes}"
                )

            codes = codes.replace(",", " ")
            await self._lookin_protocol.send_ir(format=format, codes=codes)
