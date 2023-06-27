"""The FiveM integration."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

from fivem import FiveMServerOfflineError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import (
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import FiveMDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FiveM from a config entry."""
    _LOGGER.debug(
        "Create FiveM server instance for '%s:%s'",
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
    )

    coordinator = FiveMDataUpdateCoordinator(hass, entry.data, entry.entry_id)

    try:
        await coordinator.initialize()
    except FiveMServerOfflineError as err:
        raise ConfigEntryNotReady from err

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


@dataclass
class FiveMEntityDescription(EntityDescription):
    """Describes FiveM entity."""

    extra_attrs: list[str] | None = None


class FiveMEntity(CoordinatorEntity[FiveMDataUpdateCoordinator]):
    """Representation of a FiveM base entity."""

    entity_description: FiveMEntityDescription

    def __init__(
        self,
        coordinator: FiveMDataUpdateCoordinator,
        description: FiveMEntityDescription,
    ) -> None:
        """Initialize base entity."""
        super().__init__(coordinator)
        self.entity_description = description

        self._attr_name = f"{self.coordinator.host} {description.name}"
        self._attr_unique_id = f"{self.coordinator.unique_id}-{description.key}".lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.unique_id)},
            manufacturer=MANUFACTURER,
            model=self.coordinator.server,
            name=self.coordinator.host,
            sw_version=self.coordinator.version,
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the extra attributes of the sensor."""
        if self.entity_description.extra_attrs is None:
            return None

        return {
            attr: self.coordinator.data[attr]
            for attr in self.entity_description.extra_attrs
        }
