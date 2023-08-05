"""Support for Enphase Envoy solar energy monitor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime
import logging
from typing import cast

from pyenphase import EnvoyInverter

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EnphaseUpdateCoordinator

ICON = "mdi:flash"
_LOGGER = logging.getLogger(__name__)

INVERTERS_KEY = "inverters"
LAST_REPORTED_KEY = "last_reported"


@dataclass
class EnvoyInverterRequiredKeysMixin:
    """Mixin for required keys."""

    value_fn: Callable[[EnvoyInverter], datetime.datetime | float | None]


@dataclass
class EnvoyInverterSensorEntityDescription(
    SensorEntityDescription, EnvoyInverterRequiredKeysMixin
):
    """Describes an Envoy inverter sensor entity."""


def _inverter_last_report_time(inverter: EnvoyInverter) -> datetime.datetime | None:
    """Return the last reported time for an inverter."""
    report_time = inverter.last_report_date
    if (last_reported_dt := dt_util.parse_datetime(report_time)) is None:
        return None
    if last_reported_dt.tzinfo is None:
        return last_reported_dt.replace(tzinfo=dt_util.UTC)
    return last_reported_dt


def _inverter_current_power(inverter: EnvoyInverter) -> float:
    """Return the current power for an inverter."""
    return inverter.last_report_watts


INVERTER_SENSORS = (
    EnvoyInverterSensorEntityDescription(
        key=INVERTERS_KEY,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        value_fn=_inverter_current_power,
    ),
    EnvoyInverterSensorEntityDescription(
        key=LAST_REPORTED_KEY,
        name="Last Reported",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=_inverter_last_report_time,
    ),
)

PRODUCTION_SENSORS = (
    SensorEntityDescription(
        key="production",
        name="Current Power Production",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    SensorEntityDescription(
        key="daily_production",
        name="Today's Energy Production",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="seven_days_production",
        name="Last Seven Days Energy Production",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="lifetime_production",
        name="Lifetime Energy Production",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
)

CONSUMPTION_SENSORS = (
    SensorEntityDescription(
        key="consumption",
        name="Current Power Consumption",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    SensorEntityDescription(
        key="daily_consumption",
        name="Today's Energy Consumption",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="seven_days_consumption",
        name="Last Seven Days Energy Consumption",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="lifetime_consumption",
        name="Lifetime Energy Consumption",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up envoy sensor platform."""
    coordinator: EnphaseUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    envoy_data = coordinator.envoy.data
    assert envoy_data is not None
    envoy_serial_num = config_entry.unique_id
    assert envoy_serial_num is not None
    _LOGGER.debug("Envoy data: %s", envoy_data)

    entities: list[EnvoyEntity | EnvoyInverterEntity] = [
        EnvoyEntity(coordinator, description) for description in PRODUCTION_SENSORS
    ]
    if envoy_data.system_consumption:
        entities.extend(
            EnvoyEntity(coordinator, description) for description in CONSUMPTION_SENSORS
        )
    if envoy_data.inverters:
        entities.extend(
            EnvoyInverterEntity(coordinator, description, inverter)
            for description in INVERTER_SENSORS
            for inverter in envoy_data.inverters
        )

    async_add_entities(entities)


class EnvoyEntity(CoordinatorEntity[EnphaseUpdateCoordinator], SensorEntity):
    """Envoy inverter entity."""

    _attr_icon = ICON

    def __init__(
        self,
        coordinator: EnphaseUpdateCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize Envoy entity."""
        self.entity_description = description
        envoy_name = coordinator.name
        envoy_serial_num = coordinator.envoy.serial_number
        assert envoy_serial_num is not None
        self._attr_name = f"{envoy_name} {description.name}"
        self._attr_unique_id = f"{envoy_serial_num}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, envoy_serial_num)},
            manufacturer="Enphase",
            model="Envoy",
            name=envoy_name,
        )
        super().__init__(coordinator)

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if (value := self.coordinator.data.get(self.entity_description.key)) is None:
            return None
        return cast(float, value)


class EnvoyInverterEntity(CoordinatorEntity[EnphaseUpdateCoordinator], SensorEntity):
    """Envoy inverter entity."""

    _attr_icon = ICON
    entity_description: EnvoyInverterSensorEntityDescription

    def __init__(
        self,
        coordinator: EnphaseUpdateCoordinator,
        description: EnvoyInverterSensorEntityDescription,
        serial_number: str,
    ) -> None:
        """Initialize Envoy inverter entity."""
        self.entity_description = description
        envoy_name = coordinator.name
        envoy_serial_num = coordinator.envoy.serial_number
        assert envoy_serial_num is not None
        self._serial_number = serial_number
        if description.name is not UNDEFINED:
            self._attr_name = (
                f"{envoy_name} Inverter {serial_number} {description.name}"
            )
        else:
            self._attr_name = f"{envoy_name} Inverter {serial_number}"
        if description.key == INVERTERS_KEY:
            self._attr_unique_id = serial_number
        else:
            self._attr_unique_id = f"{serial_number}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial_number)},
            name=f"Inverter {serial_number}",
            manufacturer="Enphase",
            model="Inverter",
            via_device=(DOMAIN, envoy_serial_num),
        )
        super().__init__(coordinator)

    @property
    def native_value(self) -> datetime.datetime | float | None:
        """Return the state of the sensor."""
        envoy = self.coordinator.envoy
        assert envoy.data is not None
        assert envoy.data.inverters is not None
        inverter = envoy.data.inverters[self._serial_number]
        return self.entity_description.value_fn(inverter)
