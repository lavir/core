"""Bluetooth scanner for esphome."""
from __future__ import annotations

import logging

from aioesphomeapi import BluetoothLEAdvertisement, BluetoothLERawAdvertisement
from bluetooth_data_tools import int_to_bluetooth_address, parse_advertisement_data

from homeassistant.components.bluetooth import MONOTONIC_TIME, BaseHaRemoteScanner
from homeassistant.core import callback

_LOGGER = logging.getLogger(__name__)


class ESPHomeScanner(BaseHaRemoteScanner):
    """Scanner for esphome."""

    @callback
    def async_on_advertisement(self, adv: BluetoothLEAdvertisement) -> None:
        """Call the registered callback."""
        # The mac address is a uint64, but we need a string
        self._async_on_advertisement(
            int_to_bluetooth_address(adv.address),
            adv.rssi,
            adv.name,
            adv.service_uuids,
            adv.service_data,
            adv.manufacturer_data,
            None,
            {"address_type": adv.address_type},
            MONOTONIC_TIME(),
        )

    @callback
    def async_on_raw_advertisements(
        self, advertisements: list[BluetoothLERawAdvertisement]
    ) -> None:
        """Call the registered callback."""
        now = MONOTONIC_TIME()
        for adv in advertisements:
            _LOGGER.debug("Raw advertisement: %s", adv.data)
            parsed = parse_advertisement_data((adv.data,))
            self._async_on_advertisement(
                int_to_bluetooth_address(adv.address),
                adv.rssi,
                parsed.local_name,
                parsed.service_uuids,
                parsed.service_data,
                parsed.manufacturer_data,
                None,
                {"address_type": adv.address_type},
                now,
            )
