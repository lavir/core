"""The Bluetooth integration."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import dataclasses
import logging
import time
from typing import Any, Generic, TypeVar

from home_assistant_bluetooth import BluetoothServiceInfo

from homeassistant.const import ATTR_IDENTIFIERS, ATTR_NAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    async_register_callback,
    async_track_unavailable,
)
from .const import DOMAIN


@dataclasses.dataclass(frozen=True)
class PassiveBluetoothEntityKey:
    """Key for a passive bluetooth entity.

    Example:
    key: temperature
    device_id: outdoor_sensor_1
    """

    key: str
    device_id: str | None


_T = TypeVar("_T")


@dataclasses.dataclass(frozen=True)
class PassiveBluetoothDataUpdate(Generic[_T]):
    """Generic bluetooth data."""

    devices: dict[str | None, DeviceInfo] = dataclasses.field(default_factory=dict)
    entity_descriptions: Mapping[
        PassiveBluetoothEntityKey, EntityDescription
    ] = dataclasses.field(default_factory=dict)
    entity_names: Mapping[PassiveBluetoothEntityKey, str | None] = dataclasses.field(
        default_factory=dict
    )
    entity_data: Mapping[PassiveBluetoothEntityKey, _T] = dataclasses.field(
        default_factory=dict
    )


class PassiveBluetoothDataUpdateCoordinator:
    """Passive bluetooth data update coordinator for bluetooth advertisements.

    The coordinator is responsible for dispatching the bluetooth data,
    to each processor, and tracking devices.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        address: str,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.logger = logger
        self.name: str | None = None
        self.address = address
        self._processors: list[PassiveBluetoothDataProcessor] = []
        self._cancel_track_unavailable: CALLBACK_TYPE | None = None
        self._cancel_bluetooth_advertisements: CALLBACK_TYPE | None = None
        self._present = False
        self.last_seen = 0.0

    @property
    def available(self) -> bool:
        """Return if the device is available."""
        return self._present

    @callback
    def _async_start(self) -> None:
        """Start the callbacks."""
        self._cancel_bluetooth_advertisements = async_register_callback(
            self.hass,
            self._async_handle_bluetooth_event,
            BluetoothCallbackMatcher(address=self.address),
        )
        self._cancel_track_unavailable = async_track_unavailable(
            self.hass,
            self._async_handle_unavailable,
            self.address,
        )

    @callback
    def _async_stop(self) -> None:
        """Stop the callbacks."""
        if self._cancel_bluetooth_advertisements is not None:
            self._cancel_bluetooth_advertisements()
            self._cancel_bluetooth_advertisements = None
        if self._cancel_track_unavailable is not None:
            self._cancel_track_unavailable()
            self._cancel_track_unavailable = None

    @callback
    def async_register_processor(
        self, processor: PassiveBluetoothDataProcessor
    ) -> Callable[[], None]:
        """Register a processor that subscribes to updates."""
        processor.coordinator = self

        @callback
        def remove_processor() -> None:
            """Remove a processor."""
            self._processors.remove(processor)
            self._async_handle_processors_changed()

        self._processors.append(processor)
        self._async_handle_processors_changed()
        return remove_processor

    @callback
    def _async_handle_processors_changed(self) -> None:
        """Handle processors changed."""
        running = bool(self._cancel_bluetooth_advertisements)
        if running and not self._processors:
            self._async_stop()
        elif not running and self._processors:
            self._async_start()

    @callback
    def _async_handle_unavailable(self, _address: str) -> None:
        """Handle the device going unavailable."""
        self._present = False
        for processor in self._processors:
            processor.async_handle_unavailable()

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfo,
        change: BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.last_seen = time.monotonic()
        self.name = service_info.name
        self._present = True
        if self.hass.is_stopping:
            return
        for processor in self._processors:
            processor.async_handle_bluetooth_event(service_info, change)


_PassiveBluetoothDataProcessorT = TypeVar(
    "_PassiveBluetoothDataProcessorT",
    bound="PassiveBluetoothDataProcessor[Any]",
)


class PassiveBluetoothDataProcessor(Generic[_T]):
    """Passive bluetooth data processor for bluetooth advertisements.

    The processor is responsible for keeping track of the bluetooth data
    and updating subscribers.

    The update_method must return a PassiveBluetoothDataUpdate object. Callers
    are responsible for formatting the data returned from their parser into
    the appropriate format.

    The processor will call the update_method every time the bluetooth device
    receives a new advertisement data from the coordinator with the following signature:

    update_method(service_info: BluetoothServiceInfo) -> PassiveBluetoothDataUpdate

    As the size of each advertisement is limited, the update_method should
    return a PassiveBluetoothDataUpdate object that contains only data that
    should be updated. The coordinator will then dispatch subscribers based
    on the data in the PassiveBluetoothDataUpdate object. The accumulated data
    is available in the devices, entity_data, and entity_descriptions attributes.
    """

    coordinator: PassiveBluetoothDataUpdateCoordinator

    def __init__(
        self,
        update_method: Callable[[BluetoothServiceInfo], PassiveBluetoothDataUpdate[_T]],
    ) -> None:
        """Initialize the coordinator."""
        self.coordinator: PassiveBluetoothDataUpdateCoordinator
        self._listeners: list[
            Callable[[PassiveBluetoothDataUpdate[_T] | None], None]
        ] = []
        self._entity_key_listeners: dict[
            PassiveBluetoothEntityKey,
            list[Callable[[PassiveBluetoothDataUpdate[_T] | None], None]],
        ] = {}
        self.update_method = update_method
        self.entity_names: dict[PassiveBluetoothEntityKey, str | None] = {}
        self.entity_data: dict[PassiveBluetoothEntityKey, _T] = {}
        self.entity_descriptions: dict[
            PassiveBluetoothEntityKey, EntityDescription
        ] = {}
        self.devices: dict[str | None, DeviceInfo] = {}
        self.last_update_success = True

    @property
    def available(self) -> bool:
        """Return if the device is available."""
        return self.coordinator.available and self.last_update_success

    @callback
    def async_handle_unavailable(self) -> None:
        """Handle the device going unavailable."""
        self.async_update_listeners(None)

    @callback
    def async_add_entities_listener(
        self,
        entity_class: type[PassiveBluetoothProcessorEntity],
        async_add_entites: AddEntitiesCallback,
    ) -> Callable[[], None]:
        """Add a listener for new entities."""
        created: set[PassiveBluetoothEntityKey] = set()

        @callback
        def _async_add_or_update_entities(
            data: PassiveBluetoothDataUpdate[_T] | None,
        ) -> None:
            """Listen for new entities."""
            if data is None:
                return
            entities: list[PassiveBluetoothProcessorEntity] = []
            for entity_key, description in data.entity_descriptions.items():
                if entity_key not in created:
                    entities.append(entity_class(self, entity_key, description))
                    created.add(entity_key)
            if entities:
                async_add_entites(entities)

        return self.async_add_listener(_async_add_or_update_entities)

    @callback
    def async_add_listener(
        self,
        update_callback: Callable[[PassiveBluetoothDataUpdate[_T] | None], None],
    ) -> Callable[[], None]:
        """Listen for all updates."""

        @callback
        def remove_listener() -> None:
            """Remove update listener."""
            self._listeners.remove(update_callback)

        self._listeners.append(update_callback)
        return remove_listener

    @callback
    def async_add_entity_key_listener(
        self,
        update_callback: Callable[[PassiveBluetoothDataUpdate[_T] | None], None],
        entity_key: PassiveBluetoothEntityKey,
    ) -> Callable[[], None]:
        """Listen for updates by device key."""

        @callback
        def remove_listener() -> None:
            """Remove update listener."""
            self._entity_key_listeners[entity_key].remove(update_callback)
            if not self._entity_key_listeners[entity_key]:
                del self._entity_key_listeners[entity_key]

        self._entity_key_listeners.setdefault(entity_key, []).append(update_callback)
        return remove_listener

    @callback
    def async_update_listeners(
        self, data: PassiveBluetoothDataUpdate[_T] | None
    ) -> None:
        """Update all registered listeners."""
        # Dispatch to listeners without a filter key
        for update_callback in self._listeners:
            update_callback(data)

        # Dispatch to listeners with a filter key
        for listeners in self._entity_key_listeners.values():
            for update_callback in listeners:
                update_callback(data)

    @callback
    def async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfo,
        change: BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        try:
            new_data = self.update_method(service_info)
        except Exception as err:  # pylint: disable=broad-except
            self.last_update_success = False
            self.coordinator.logger.exception(
                "Unexpected error updating %s data: %s", self.coordinator.name, err
            )
            return

        if not isinstance(new_data, PassiveBluetoothDataUpdate):
            self.last_update_success = False  # type: ignore[unreachable]
            raise ValueError(
                f"The update_method for {self.coordinator.name} returned {new_data} instead of a PassiveBluetoothDataUpdate"
            )

        if not self.last_update_success:
            self.last_update_success = True
            self.coordinator.logger.info(
                "Processing %s data recovered", self.coordinator.name
            )

        self.devices.update(new_data.devices)
        self.entity_descriptions.update(new_data.entity_descriptions)
        self.entity_data.update(new_data.entity_data)
        self.entity_names.update(new_data.entity_names)
        self.async_update_listeners(new_data)


class PassiveBluetoothProcessorEntity(Entity, Generic[_PassiveBluetoothDataProcessorT]):
    """A class for entities using PassiveBluetoothDataProcessor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        processor: _PassiveBluetoothDataProcessorT,
        entity_key: PassiveBluetoothEntityKey,
        description: EntityDescription,
        context: Any = None,
    ) -> None:
        """Create the entity with a PassiveBluetoothDataProcessor."""
        self.entity_description = description
        self.entity_key = entity_key
        self.processor = processor
        self.processor_context = context
        address = processor.coordinator.address
        device_id = entity_key.device_id
        devices = processor.devices
        key = entity_key.key
        if device_id in devices:
            base_device_info = devices[device_id]
        else:
            base_device_info = DeviceInfo({})
        if device_id:
            self._attr_device_info = base_device_info | DeviceInfo(
                {ATTR_IDENTIFIERS: {(DOMAIN, f"{address}-{device_id}")}}
            )
            self._attr_unique_id = f"{address}-{key}-{device_id}"
        else:
            self._attr_device_info = base_device_info | DeviceInfo(
                {ATTR_IDENTIFIERS: {(DOMAIN, address)}}
            )
            self._attr_unique_id = f"{address}-{key}"
        if ATTR_NAME not in self._attr_device_info:
            self._attr_device_info[ATTR_NAME] = self.processor.coordinator.name
        self._attr_name = processor.entity_names.get(entity_key)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.processor.available

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.processor.async_add_entity_key_listener(
                self._handle_processor_update, self.entity_key
            )
        )

    @callback
    def _handle_processor_update(
        self, new_data: PassiveBluetoothDataUpdate | None
    ) -> None:
        """Handle updated data from the processor."""
        self.async_write_ha_state()
