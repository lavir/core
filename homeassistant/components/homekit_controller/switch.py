"""Support for Homekit switches."""
from __future__ import annotations

from dataclasses import dataclass

from aiohomekit.model.characteristics import (
    Characteristic,
    CharacteristicsTypes,
    InUseValues,
    IsConfiguredValues,
)
from aiohomekit.model.services import ServicesTypes

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory

from . import KNOWN_DEVICES, CharacteristicEntity, HomeKitEntity

OUTLET_IN_USE = "outlet_in_use"

ATTR_IN_USE = "in_use"
ATTR_IS_CONFIGURED = "is_configured"
ATTR_REMAINING_DURATION = "remaining_duration"


@dataclass
class DeclarativeSwitchEntityDescription(SwitchEntityDescription):
    """Describes Homekit button."""

    true_value: bool = True
    false_value: bool = False


SWITCH_ENTITIES: dict[str, DeclarativeSwitchEntityDescription] = {
    CharacteristicsTypes.Vendor.AQARA_PAIRING_MODE: DeclarativeSwitchEntityDescription(
        key=CharacteristicsTypes.Vendor.AQARA_PAIRING_MODE,
        name="Pairing Mode",
        icon="mdi:lock-open",
        entity_category=EntityCategory.CONFIG,
    ),
    CharacteristicsTypes.Vendor.AQARA_E1_PAIRING_MODE: DeclarativeSwitchEntityDescription(
        key=CharacteristicsTypes.Vendor.AQARA_E1_PAIRING_MODE,
        name="Pairing Mode",
        icon="mdi:lock-open",
        entity_category=EntityCategory.CONFIG,
    ),
}


class HomeKitSwitch(HomeKitEntity, SwitchEntity):
    """Representation of a Homekit switch."""

    def get_characteristic_types(self):
        """Define the homekit characteristics the entity cares about."""
        return [CharacteristicsTypes.ON, CharacteristicsTypes.OUTLET_IN_USE]

    @property
    def is_on(self):
        """Return true if device is on."""
        return self.service.value(CharacteristicsTypes.ON)

    async def async_turn_on(self, **kwargs):
        """Turn the specified switch on."""
        await self.async_put_characteristics({CharacteristicsTypes.ON: True})

    async def async_turn_off(self, **kwargs):
        """Turn the specified switch off."""
        await self.async_put_characteristics({CharacteristicsTypes.ON: False})

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        outlet_in_use = self.service.value(CharacteristicsTypes.OUTLET_IN_USE)
        if outlet_in_use is not None:
            return {OUTLET_IN_USE: outlet_in_use}


class HomeKitValve(HomeKitEntity, SwitchEntity):
    """Represents a valve in an irrigation system."""

    def get_characteristic_types(self):
        """Define the homekit characteristics the entity cares about."""
        return [
            CharacteristicsTypes.ACTIVE,
            CharacteristicsTypes.IN_USE,
            CharacteristicsTypes.IS_CONFIGURED,
            CharacteristicsTypes.REMAINING_DURATION,
        ]

    async def async_turn_on(self, **kwargs):
        """Turn the specified valve on."""
        await self.async_put_characteristics({CharacteristicsTypes.ACTIVE: True})

    async def async_turn_off(self, **kwargs):
        """Turn the specified valve off."""
        await self.async_put_characteristics({CharacteristicsTypes.ACTIVE: False})

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:water"

    @property
    def is_on(self):
        """Return true if device is on."""
        return self.service.value(CharacteristicsTypes.ACTIVE)

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        attrs = {}

        in_use = self.service.value(CharacteristicsTypes.IN_USE)
        if in_use is not None:
            attrs[ATTR_IN_USE] = in_use == InUseValues.IN_USE

        is_configured = self.service.value(CharacteristicsTypes.IS_CONFIGURED)
        if is_configured is not None:
            attrs[ATTR_IS_CONFIGURED] = is_configured == IsConfiguredValues.CONFIGURED

        remaining = self.service.value(CharacteristicsTypes.REMAINING_DURATION)
        if remaining is not None:
            attrs[ATTR_REMAINING_DURATION] = remaining

        return attrs


class DeclarativeCharacteristicSwitch(CharacteristicEntity, SwitchEntity):
    """Representation of a Homekit switch backed by a single characteristic."""

    def __init__(
        self,
        conn,
        info,
        char,
        description: DeclarativeSwitchEntityDescription,
    ):
        """Initialise a HomeKit switch."""
        self.entity_description = description
        super().__init__(conn, info, char)

    @property
    def name(self) -> str:
        """Return the name of the device if any."""
        if prefix := super().name:
            return f"{prefix} {self.entity_description.name}"
        return self.entity_description.name

    def get_characteristic_types(self):
        """Define the homekit characteristics the entity cares about."""
        return [self._char.type]

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._char.value == self.entity_description.true_value

    async def async_turn_on(self, **kwargs):
        """Turn the specified switch on."""
        await self.async_put_characteristics(
            {self._char.type: self.entity_description.true_value}
        )

    async def async_turn_off(self, **kwargs):
        """Turn the specified switch off."""
        await self.async_put_characteristics(
            {self._char.type: self.entity_description.false_value}
        )


ENTITY_TYPES = {
    ServicesTypes.SWITCH: HomeKitSwitch,
    ServicesTypes.OUTLET: HomeKitSwitch,
    ServicesTypes.VALVE: HomeKitValve,
}


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Homekit switches."""
    hkid = config_entry.data["AccessoryPairingID"]
    conn = hass.data[KNOWN_DEVICES][hkid]

    @callback
    def async_add_service(service):
        if not (entity_class := ENTITY_TYPES.get(service.short_type)):
            return False
        info = {"aid": service.accessory.aid, "iid": service.iid}
        async_add_entities([entity_class(conn, info)], True)
        return True

    conn.add_listener(async_add_service)

    @callback
    def async_add_characteristic(char: Characteristic):
        if not (description := SWITCH_ENTITIES.get(char.type)):
            return False

        info = {"aid": char.service.accessory.aid, "iid": char.service.iid}
        async_add_entities(
            [DeclarativeCharacteristicSwitch(conn, info, char, description)], True
        )
        return True

    conn.add_char_factory(async_add_characteristic)
