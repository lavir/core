"""Test ESPHome climates."""


from unittest.mock import call

from aioesphomeapi import (
    APIClient,
    ClimateAction,
    ClimateFanMode,
    ClimateInfo,
    ClimateMode,
    ClimateState,
    ClimateSwingMode,
)

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant


async def test_climate_entity(
    hass: HomeAssistant, mock_client: APIClient, mock_generic_device_entry
) -> None:
    """Test a generic climate entity."""
    entity_info = [
        ClimateInfo(
            object_id="myclimate",
            key=1,
            name="my climate",
            unique_id="my_climate",
            supports_current_temperature=True,
            supports_two_point_target_temperature=True,
            supports_action=True,
            visual_min_temperature=10.0,
            visual_max_temperature=30.0,
        )
    ]
    states = [
        ClimateState(
            key=1,
            mode=ClimateMode.COOL,
            action=ClimateAction.COOLING,
            current_temperature=30,
            target_temperature=20,
            fan_mode=ClimateFanMode.AUTO,
            swing_mode=ClimateSwingMode.BOTH,
        )
    ]
    user_service = []
    await mock_generic_device_entry(
        mock_client=mock_client,
        entity_info=entity_info,
        user_service=user_service,
        states=states,
    )
    state = hass.states.get("climate.test_my_climate")
    assert state is not None
    assert state.state == HVACMode.COOL

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: "climate.test_my_climate", ATTR_TEMPERATURE: 25},
        blocking=True,
    )
    mock_client.climate_command.assert_has_calls([call(key=1, target_temperature=25.0)])
    mock_client.climate_command.reset_mock()
