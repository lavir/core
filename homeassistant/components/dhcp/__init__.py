"""The dhcp integration."""

import fnmatch
import logging

from scapy.all import DHCP, AsyncSniffer, Ether

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac
from homeassistant.loader import async_get_dhcp

from .const import DOMAIN

FILTER = "udp and (port 67 or 68)"
REQUESTED_ADDR = "requested_addr"
HOSTNAME = "hostname"
MACADDRESS = "macaddress"
IP_ADDRESS = "ip_address"


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the dhcp component."""

    async def _initialize(_):
        dhcp_watcher = DHCPWatcher(hass, await async_get_dhcp(hass))
        try:
            scapy_sniffer = AsyncSniffer(
                filter=FILTER, prn=dhcp_watcher.handle_dhcp_packet
            )
            scapy_sniffer.start()
        except Exception as ex:
            _LOGGER.info("Cannot watch for dhcp packets: %s", ex)
            return

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, scapy_sniffer.stop)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _initialize)
    return True


class DHCPWatcher:
    """Class to watch dhcp requests."""

    def __init__(self, hass, integration_matchers):
        """Initialize class."""
        self.hass = hass
        self._integration_matchers = integration_matchers
        self._address_data = {}

    def handle_dhcp_packet(self, packet):
        """Process a dhcp packet."""
        if DHCP not in packet:
            return

        options = packet[DHCP].options
        if options[0][1] != 3:
            # DHCP request
            return

        try:
            ip_address = _decode_dhcp_option(options, REQUESTED_ADDR)
            hostname = _decode_dhcp_option(options, HOSTNAME)
            mac = format_mac(packet[Ether].src)
            _LOGGER.warning(f"Host {hostname} ({mac}) requested {ip_address}")
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.debug("Error decoding DHCP packet: %s", ex)
            return

        data = self._address_data.get(ip_address)

        if data and data[MACADDRESS] == mac and data[HOSTNAME] == hostname:
            return

        self._address_data[ip_address] = {MACADDRESS: mac, HOSTNAME: hostname}

        if data[MACADDRESS] is None or data[HOSTNAME] is None:
            return

        lowercase_name = hostname.lower()
        uppercase_mac = mac.upper()

        for entry in self._integration_matchers:
            if MACADDRESS in entry and not fnmatch.fnmatch(
                uppercase_mac, entry[MACADDRESS]
            ):
                continue

            if HOSTNAME in entry and not fnmatch.fnmatch(
                lowercase_name, entry[HOSTNAME]
            ):
                continue

            self.hass.add_job(
                self.hass.config_entries.flow.async_init(
                    entry["domain"],
                    context={"source": DOMAIN},
                    data={IP_ADDRESS: ip_address, **data[ip_address]},
                )
            )

        return


def _decode_dhcp_option(dhcp_options, key):
    """Extract and decode data from a packet option."""
    for i in dhcp_options:
        if i[0] != key:
            continue
        # hostname is unicode
        return i[1].decode() if key == HOSTNAME else i[i]
