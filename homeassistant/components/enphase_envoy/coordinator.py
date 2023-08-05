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

    envoy_serial_number: str
    name: str

    def __init__(
        self,
        hass: HomeAssistant,
        envoy: Envoy,
        name: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize DataUpdateCoordinator to gather data for specific SmartPlug."""
        self.envoy = envoy
        self.username = username
        self.password = password
        self._default_name = name
        self._setup_complete = False
        super().__init__(
            hass,
            _LOGGER,
            name=self.name,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )

    async def _async_setup_and_authenticate(self) -> None:
        """Set up and authenticate with the envoy."""
        envoy = self.envoy
        await envoy.setup()
        await envoy.authenticate(username=self.username, password=self.password)
        assert envoy.serial_number is not None
        self.envoy_serial_number = envoy.serial_number
        self.name = self._default_name or f"Envoy {self.envoy_serial_number}"
        self._setup_complete = True

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all device and sensor data from api."""
        envoy = self.envoy
        try:
            if not self._setup_complete:
                await self._async_setup_and_authenticate()
            return (await envoy.update()).raw
        except (EnvoyAuthenticationError, EnvoyAuthenticationRequired) as err:
            raise ConfigEntryAuthFailed from err
        except EnvoyError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
