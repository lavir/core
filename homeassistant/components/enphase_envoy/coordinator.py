"""The enphase_envoy component."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from pyenphase import (
    Envoy,
    EnvoyAuthenticationError,
    EnvoyAuthenticationRequired,
    EnvoyError,
)

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

SCAN_INTERVAL = timedelta(seconds=60)
_LOGGER = logging.getLogger(__name__)


class EnphaseUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """DataUpdateCoordinator to gather data from any envoy."""

    def __init__(
        self,
        hass: HomeAssistant,
        envoy: Envoy,
        name: str,
    ) -> None:
        """Initialize DataUpdateCoordinator to gather data for specific SmartPlug."""
        self.envoy = envoy
        self.envoy_serial_number = envoy.serial_number
        self.name = name or f"Envoy {self.envoy_serial_number}"
        super().__init__(
            hass,
            _LOGGER,
            name=self.name,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all device and sensor data from api."""
        try:
            return (await self.envoy.update()).raw
        except (EnvoyAuthenticationError, EnvoyAuthenticationRequired) as err:
            raise ConfigEntryAuthFailed from err
        except EnvoyError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
