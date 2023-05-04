"""Support for a ScreenLogic Binary Sensor."""
from dataclasses import dataclass
import logging

from screenlogicpy.const.common import DEVICE_TYPE, ON_OFF
from screenlogicpy.const.data import ATTR, DEVICE, GROUP, VALUE

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ScreenlogicDataUpdateCoordinator
from .const import DOMAIN
from .data import EntityParameter, TemplateData, process_entity
from .entity import (
    ScreenlogicEntity,
    ScreenLogicEntityDescription,
    ScreenLogicPushEntity,
    ScreenLogicPushEntityDescription,
)

_LOGGER = logging.getLogger(__name__)

SUPPORTED_DATA: TemplateData = {
    DEVICE.CONTROLLER: {
        GROUP.SENSOR: {
            VALUE.ACTIVE_ALERT: {},
            VALUE.CLEANER_DELAY: {},
            VALUE.FREEZE_MODE: {},
            VALUE.POOL_DELAY: {},
            VALUE.SPA_DELAY: {},
        },
    },
    DEVICE.PUMP: {
        "*": {
            VALUE.STATE: {},
        },
    },
    DEVICE.INTELLICHEM: {
        GROUP.ALARM: {
            VALUE.FLOW_ALARM: {},
            VALUE.ORP_HIGH_ALARM: {},
            VALUE.ORP_LOW_ALARM: {},
            VALUE.ORP_SUPPLY_ALARM: {},
            VALUE.PH_HIGH_ALARM: {},
            VALUE.PH_LOW_ALARM: {},
            VALUE.PH_SUPPLY_ALARM: {},
            VALUE.PROBE_FAULT_ALARM: {},
        },
        GROUP.ALERT: {
            VALUE.ORP_LIMIT: {},
            VALUE.PH_LIMIT: {},
            VALUE.PH_LOCKOUT: {},
        },
        GROUP.WATER_BALANCE: {
            VALUE.CORROSIVE: {},
            VALUE.SCALING: {},
        },
    },
    DEVICE.SCG: {
        GROUP.SENSOR: {
            VALUE.STATE: {},
        },
    },
}


SL_DEVICE_TYPE_TO_HA_DEVICE_CLASS = {DEVICE_TYPE.ALARM: BinarySensorDeviceClass.PROBLEM}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    entities: list[ScreenLogicBinarySensor] = []
    coordinator: ScreenlogicDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    gateway = coordinator.gateway

    for (
        data_path,
        enabled,
        entity_data,
        entity_key,
        sub_code,
        value_params,
    ) in process_entity(gateway, SUPPORTED_DATA):
        base_kwargs = {
            "data_path": data_path,
            "key": entity_key,
            "device_class": SL_DEVICE_TYPE_TO_HA_DEVICE_CLASS.get(
                entity_data.get(ATTR.DEVICE_TYPE)
            ),
            "entity_category": value_params.get(
                EntityParameter.ENTITY_CATEGORY, EntityCategory.DIAGNOSTIC
            ),
            "entity_registry_enabled_default": enabled,
            "name": entity_data.get(ATTR.NAME),
        }

        entities.append(
            ScreenLogicPushBinarySensor(
                coordinator,
                ScreenLogicPushBinarySensorDescription(
                    subscription_code=sub_code, **base_kwargs
                ),
            )
            if sub_code
            else ScreenLogicBinarySensor(
                coordinator, ScreenLogicBinarySensorDescription(**base_kwargs)
            )
        )

    async_add_entities(entities)


@dataclass
class ScreenLogicBinarySensorDescription(
    BinarySensorEntityDescription, ScreenLogicEntityDescription
):
    """A class that describes ScreenLogic binary sensor eneites."""


class ScreenLogicBinarySensor(ScreenlogicEntity, BinarySensorEntity):
    """Base class for all ScreenLogic binary sensor entities."""

    entity_description: ScreenLogicBinarySensorDescription
    _attr_has_entity_name = True

    @property
    def is_on(self) -> bool:
        """Determine if the sensor is on."""
        return self.entity_data[ATTR.VALUE] == ON_OFF.ON


@dataclass
class ScreenLogicPushBinarySensorDescription(
    ScreenLogicBinarySensorDescription, ScreenLogicPushEntityDescription
):
    """Describes a ScreenLogicPushBinarySensor."""


class ScreenLogicPushBinarySensor(ScreenLogicPushEntity, ScreenLogicBinarySensor):
    """Representation of a basic ScreenLogic sensor entity."""

    entity_description: ScreenLogicPushBinarySensorDescription
