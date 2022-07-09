"""The Govee Bluetooth integration."""
from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity

BluetoothListenerCallbackType = Callable[[dict[str, Any]], None]


class BluetoothDataUpdateCoordinator:
    """Bluetooth data update dispatcher."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        address: str,
        *,
        name: str,
        parser_method: Callable[
            [bluetooth.BluetoothServiceInfo, bluetooth.BluetoothChange],
            dict[str, Any] | None,
        ]
        | None = None,
    ) -> None:
        """Initialize the dispatcher."""
        self.hass = hass
        self.logger = logger
        self.name = name
        self.parser_method = parser_method
        self.address = address
        self._listeners: dict[str | None, list[BluetoothListenerCallbackType]] = {}
        self._cancel: CALLBACK_TYPE | None = None
        self.last_update_success = True
        self.last_exception: Exception | None = None

    @callback
    def async_setup(self) -> CALLBACK_TYPE:
        """Start the callback."""
        if self.parser_method is None:
            raise NotImplementedError("Parser method not implemented")
        return bluetooth.async_register_callback(
            self.hass,
            self._async_handle_bluetooth_event,
            bluetooth.BluetoothCallbackMatcher(address=self.address),
        )

    @callback
    def async_add_listener(
        self, update_callback: BluetoothListenerCallbackType, key: str | None = None
    ) -> Callable[[], None]:
        """Listen for data updates."""

        @callback
        def remove_listener() -> None:
            """Remove update listener."""
            self._listeners[key].remove(update_callback)
            if not self._listeners[key]:
                del self._listeners[key]

        self._listeners.setdefault(key, []).append(update_callback)
        return remove_listener

    @callback
    def async_update_listeners(self, data: dict[str, Any]) -> None:
        """Update all registered listeners."""
        self.logger.warning("Updating listeners %s with data %s", self._listeners, data)

        # Dispatch to listeners without a filter key
        if listeners := self._listeners.get(None):
            for update_callback in listeners:
                update_callback(data)

        # Dispatch to listeners with a filter key
        for key in data:
            if listeners := self._listeners.get(key):
                for update_callback in listeners:
                    update_callback(data)

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfo,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.logger.warning(
            "_async_handle_bluetooth_event: %s %s", service_info, change
        )
        assert self.parser_method is not None
        try:
            parsed = self.parser_method(service_info, change)
        except Exception as err:  # pylint: disable=broad-except
            self.last_exception = err
            self.last_update_success = False
            self.logger.exception("Unexpected error update %s data: %s", self.name, err)
        else:
            if not self.last_update_success:
                self.last_update_success = True
                self.logger.info("Fetching %s data recovered", self.name)
            if parsed:
                self.async_update_listeners(parsed)


class BluetoothCoordinatorEntity(entity.Entity):
    """A class for entities using DataUpdateCoordinator."""

    def __init__(
        self,
        coordinator: BluetoothDataUpdateCoordinator,
        description: entity.EntityDescription,
        data: dict[str, Any],
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        self.coordinator = coordinator
        self.entity_description = description
        self.data = data
        self._attr_unique_id = f"{coordinator.address}-{description.key}"
        self._attr_name = f"{coordinator.name} {description.name}"
        self._attr_device_info = entity.DeviceInfo(
            name=coordinator.name,
            connections={(bluetooth.DOMAIN, coordinator.address)},
            # TODO: They may have multiple devices such as an outdoor sensor
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll. Coordinator notifies entity of updates."""
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # TODO: be able to set some type of timeout for last update
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(
                self._handle_coordinator_update, self.entity_description.key
            )
        )

    @callback
    def _handle_coordinator_update(self, data: dict[str, Any]) -> None:
        """Handle updated data from the coordinator."""
        self.data = data
        self.async_write_ha_state()
