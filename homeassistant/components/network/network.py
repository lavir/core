"""Network helper class for the network integration."""
from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address
import logging
from typing import Any, Iterable, cast

import ifaddr
from pyroute2 import IPRoute

from homeassistant.core import HomeAssistant, callback

from .const import (
    ATTR_CONFIGURED_ADAPTERS,
    DEFAULT_CONFIGURED_ADAPTERS,
    MDNS_TARGET_IP,
    NETWORK_CONFIG_SCHEMA,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .models import Adapter, IPv4ConfiguredAddress, IPv6ConfiguredAddress

_LOGGER = logging.getLogger(__name__)


ZC_CONF_DEFAULT_INTERFACE = (
    "default_interface"  # cannot import from zeroconf due to circular dep
)


class Network:
    """Network helper class for the network integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the Network class."""
        self.hass = hass
        self._store = hass.helpers.storage.Store(STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {}
        self._adapters: list[Adapter] = []
        self._next_broadcast_hop: str | None = None

    @property
    def adapters(self) -> list[Adapter]:
        """Return the list of adapters."""
        return self._adapters

    @property
    def configured_adapters(self) -> list[str]:
        """Return the configured adapters."""
        return self._data.get(ATTR_CONFIGURED_ADAPTERS, DEFAULT_CONFIGURED_ADAPTERS)

    async def async_setup(self) -> None:
        """Set up the network config."""
        self._next_broadcast_hop = await async_default_next_broadcast_hop(self.hass)
        await self.async_load()
        self._adapters = load_adapters(self._next_broadcast_hop)

    async def async_migrate_from_zeroconf(self, zc_config: dict[str, Any]) -> None:
        """Migrate configuration from zeroconf."""
        if self._data:
            return

        if (
            ZC_CONF_DEFAULT_INTERFACE in zc_config
            and not zc_config[ZC_CONF_DEFAULT_INTERFACE]
        ):
            self._data[ATTR_CONFIGURED_ADAPTERS] = _adapters_with_exernal_addresses(
                self._adapters
            )
            await self._async_save()

    @callback
    def async_configure(self) -> None:
        """Configure from storage."""
        if not _enable_adapters(self._adapters, self.configured_adapters):
            _enable_auto_detected_adapters(self._adapters)

    async def async_reconfig(self, config: dict[str, Any]) -> None:
        """Reconfigure network."""
        config = NETWORK_CONFIG_SCHEMA(config)
        self._data[ATTR_CONFIGURED_ADAPTERS] = config[ATTR_CONFIGURED_ADAPTERS]
        for adapter in self._adapters:
            adapter["enabled"] = False
        _enable_adapters(self._adapters, self.configured_adapters)
        await self._async_save()

    async def async_load(self) -> None:
        """Load config."""
        if stored := await self._store.async_load():
            self._data = cast(dict, stored)

    async def _async_save(self) -> None:
        """Save preferences."""
        await self._store.async_save(self._data)


def _enable_adapters(adapters: list[Adapter], enabled_interfaces: list[str]) -> bool:
    """Enable configured adapters."""
    if not enabled_interfaces:
        return False

    found_adapter = False
    for adapter in adapters:
        if adapter["name"] in enabled_interfaces:
            adapter["enabled"] = True
            found_adapter = True

    return found_adapter


def _enable_auto_detected_adapters(adapters: list[Adapter]) -> None:
    """Enable auto detected adapters."""
    _enable_adapters(
        adapters, [adapter["name"] for adapter in adapters if adapter["auto"]]
    )


def _adapters_with_exernal_addresses(adapters: list[Adapter]) -> list[str]:
    """Enable all interfaces with an external address."""
    return [
        adapter["name"]
        for adapter in adapters
        if _adapter_has_external_address(adapter)
    ]


def _adapter_has_external_address(adapter: Adapter) -> bool:
    """Adapter has a non-loopback and non-link-local address."""
    return any(
        _has_external_address(v4_config["address"]) for v4_config in adapter["ipv4"]
    ) or any(
        _has_external_address(v6_config["address"]) for v6_config in adapter["ipv6"]
    )


def _has_external_address(ip_str: str) -> bool:
    return _ip_address_is_external(ip_address(ip_str))


def _ip_address_is_external(ip_addr: IPv4Address | IPv6Address) -> bool:
    return (
        not ip_addr.is_multicast
        and not ip_addr.is_loopback
        and not ip_addr.is_link_local
    )


def load_adapters(next_hop: str | None) -> list[Adapter]:
    """Load adapters."""
    adapters = ifaddr.get_adapters()
    ha_adapters: list[Adapter] = []
    if next_hop:
        next_hop_address = ip_address(next_hop)
    default_adapter_is_auto = False

    for adapter in adapters:
        ip_v4s: list[IPv4ConfiguredAddress] = []
        ip_v6s: list[IPv6ConfiguredAddress] = []
        default = False
        auto = False

        for ip_config in adapter.ips:
            if ip_config.is_IPv6:
                try:
                    ip_addr = ip_address(ip_config.ip[0])
                except ValueError:
                    continue
                ip_v6: IPv6ConfiguredAddress = {
                    "address": str(ip_addr),
                    "flowinfo": ip_config.ip[1],
                    "scope_id": ip_config.ip[2],
                    "network_prefix": ip_config.network_prefix,
                }
                ip_v6s.append(ip_v6)
            else:
                try:
                    ip_addr = ip_address(ip_config.ip)
                except ValueError:
                    continue
                ip_v4: IPv4ConfiguredAddress = {
                    "address": str(ip_addr),
                    "network_prefix": ip_config.network_prefix,
                }
                ip_v4s.append(ip_v4)

            if next_hop and ip_addr == next_hop_address:
                default = True
            if default and _ip_address_is_external(ip_addr):
                auto = True
                default_adapter_is_auto = True

        ha_adapter: Adapter = {
            "name": adapter.nice_name,
            "enabled": False,
            "auto": auto,
            "default": default,
            "ipv4": ip_v4s,
            "ipv6": ip_v6s,
        }
        ha_adapters.append(ha_adapter)

    if not default_adapter_is_auto:
        for adapter in ha_adapters:
            if _adapter_has_external_address(adapter):
                adapter["auto"] = True

    return ha_adapters


def _get_ip_route(dst_ip: str) -> Iterable:
    """Get ip next hop."""
    return cast(Iterable, IPRoute().route("get", dst=dst_ip))


def _first_ip_nexthop_from_route(routes: Iterable) -> str | None:
    """Find the first RTA_PREFSRC in the routes."""
    _LOGGER.debug("Routes: %s", routes)
    for route in routes:
        for key, value in route["attrs"]:
            if key == "RTA_PREFSRC":
                return cast(str, value)
    return None


async def async_default_next_broadcast_hop(hass: HomeAssistant) -> None | str:
    """Auto detect the default next broadcast hop."""
    try:
        routes: Iterable = await hass.async_add_executor_job(
            _get_ip_route, MDNS_TARGET_IP
        )
    except Exception as ex:  # pylint: disable=broad-except
        _LOGGER.debug(
            "The system could not auto detect routing data on your operating system",
            exc_info=ex,
        )
        return None
    else:
        if first_ip := _first_ip_nexthop_from_route(routes):
            return first_ip

    _LOGGER.debug(
        "The system could not auto detect the nexthop for %s on your operating system",
        MDNS_TARGET_IP,
    )
    return None
