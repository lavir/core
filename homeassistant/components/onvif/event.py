"""ONVIF event abstraction."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import datetime as dt
from logging import DEBUG, WARNING

from aiohttp.web import Request
from httpx import RemoteProtocolError, RequestError, TransportError
from onvif import ONVIFCamera, ONVIFService
from onvif.client import _DEFAULT_SETTINGS
from onvif.exceptions import ONVIFError
from zeep.exceptions import Fault, XMLParseError, XMLSyntaxError
from zeep.loader import parse_xml

from homeassistant.components import webhook
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER
from .models import Event
from .parsers import PARSERS

UNHANDLED_TOPICS: set[str] = set()

SUBSCRIPTION_ERRORS = (Fault, asyncio.TimeoutError, TransportError)
SET_SYNCHRONIZATION_POINT_ERRORS = (*SUBSCRIPTION_ERRORS, TypeError)
UNSUBSCRIBE_ERRORS = (XMLParseError, *SUBSCRIPTION_ERRORS)


def _stringify_onvif_error(error: Exception) -> str:
    """Stringify ONVIF error."""
    if isinstance(error, Fault):
        return error.message or str(error) or "Device sent empty error"
    return str(error)


def _get_next_termination_time() -> str:
    """Get next termination time."""
    return (
        (dt_util.utcnow() + dt.timedelta(days=1))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class EventManager:
    """ONVIF Event Manager."""

    def __init__(
        self, hass: HomeAssistant, device: ONVIFCamera, unique_id: str
    ) -> None:
        """Initialize event manager."""
        self.hass: HomeAssistant = hass
        self.device: ONVIFCamera = device
        self.unique_id: str = unique_id
        self.started: bool = False

        self._subscription: ONVIFService = None
        self._webhook_subscription: ONVIFService = None
        self._pullpoint_service: ONVIFService = None
        self._events: dict[str, Event] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_refresh: CALLBACK_TYPE | None = None

        self.webhook_id: str | None = None
        self._base_url: str | None = None
        self._webhook_url: str | None = None
        self._notify_service: ONVIFService | None = None

    @property
    def platforms(self) -> set[str]:
        """Return platforms to setup."""
        return {event.platform for event in self._events.values()}

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> Callable[[], None]:
        """Listen for data updates."""
        # This is the first listener, set up polling.
        if not self._listeners:
            self.async_schedule_pull()

        self._listeners.append(update_callback)

        @callback
        def remove_listener() -> None:
            """Remove update listener."""
            self.async_remove_listener(update_callback)

        return remove_listener

    @callback
    def async_remove_listener(self, update_callback: CALLBACK_TYPE) -> None:
        """Remove data update."""
        if update_callback in self._listeners:
            self._listeners.remove(update_callback)

        if not self._listeners and self._unsub_refresh:
            self._unsub_refresh()
            self._unsub_refresh = None

    async def _async_start_webhook(self) -> bool:
        try:
            self._notify_service = self.device.create_notification_service()
            self._pullpoint_service = self.device.create_onvif_service("pullpoint")
            notify_subscribe = await self._notify_service.Subscribe(
                {
                    "InitialTerminationTime": _get_next_termination_time(),
                    "ConsumerReference": {"Address": self._webhook_url},
                }
            )
            # pylint: disable=protected-access
            self.device.xaddrs[
                "http://www.onvif.org/ver10/events/wsdl/WebhookSubscription"
            ] = notify_subscribe.SubscriptionReference.Address._value_1

            # Create subscription manager
            self._webhook_subscription = self.device.create_subscription_service(
                "WebhookSubscription"
            )
            return True
        except (ONVIFError, Fault, RequestError, XMLParseError) as err:
            # Do not unregister the webhook because if its still
            # subscribed to events, it will still receive them.
            LOGGER.exception(
                "%s: Device does not support notification service or too many subscriptions: %s",
                self.unique_id,
                err,
            )
        return False

    async def async_start(self) -> bool:
        """Start polling events."""
        LOGGER.debug("%s: Starting event manager", self.unique_id)
        if self.webhook_id is None:
            self.async_register_webhook()
        LOGGER.debug("%s: Webhook registered: %s", self.unique_id, self.webhook_id)
        events_via_webhook = False
        event_via_pull_point = False
        try:
            event_service = self.device.create_events_service()
            await event_service.GetServiceCapabilities()
        except (ONVIFError, Fault, RequestError, XMLParseError) as err:
            LOGGER.debug(
                "%s: Device does not support events service: %s",
                self.unique_id,
                err,
            )
        if self._webhook_url:
            events_via_webhook = await self._async_start_webhook()
        event_via_pull_point = await self._async_start_pull_point()
        # If we have a working webhook or the device supports pullpoint
        # we return true event if we failed to start one of them since
        # it may be able to start later.
        if events_via_webhook or event_via_pull_point:
            return True
        return False

    async def _async_start_pull_point(self) -> bool:
        LOGGER.debug("%s: Creating pullpoint subscription", self.unique_id)
        try:
            if not await self.device.create_pullpoint_subscription(
                {"InitialTerminationTime": _get_next_termination_time()}
            ):
                return False

            # Create subscription manager
            self._subscription = self.device.create_subscription_service(
                "PullPointSubscription"
            )

            # Renew immediately
            await self.async_renew()

            pullpoint = self.device.create_pullpoint_service()
            # Initialize events
            with suppress(*SET_SYNCHRONIZATION_POINT_ERRORS):
                await pullpoint.SetSynchronizationPoint()
            response = await pullpoint.PullMessages(
                {"MessageLimit": 100, "Timeout": dt.timedelta(seconds=5)}
            )

            # Parse event initialization
            await self.async_parse_messages(response.NotificationMessage)

            self.started = True
            return True
        except (ONVIFError, Fault, RequestError, XMLParseError) as err:
            LOGGER.debug(
                "%s: Device does not support pullpoint service: %s",
                self.unique_id,
                err,
            )
        return False

    @callback
    def async_register_webhook(self) -> None:
        """Register the webhook for motion events."""
        webhook_id = f"{DOMAIN}_{self.unique_id}_events"
        self.webhook_id = webhook_id
        try:
            self._base_url = get_url(self.hass, prefer_external=False)
        except NoURLAvailableError:
            try:
                self._base_url = get_url(self.hass, prefer_external=True)
            except NoURLAvailableError:
                self.async_unregister_webhook()

        with suppress(ValueError):
            webhook.async_register(
                self.hass, DOMAIN, webhook_id, webhook_id, self._handle_webhook
            )
        webhook_path = webhook.async_generate_path(webhook_id)
        self._webhook_url = f"{self._base_url}{webhook_path}"

        LOGGER.debug("Registered webhook: %s", webhook_id)

    @callback
    def async_unregister_webhook(self):
        """Unregister the webhook for motion events."""
        LOGGER.debug("Unregistering webhook %s", self.webhook_id)
        webhook.async_unregister(self.hass, self.webhook_id)
        self.webhook_id = None

    async def _handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> None:
        """Handle incoming webhook."""
        try:
            content = await request.read()
        except ConnectionResetError as ex:
            LOGGER.error("Error reading webhook: %s", ex)
            return

        assert self._pullpoint_service is not None
        assert self._pullpoint_service.transport is not None
        try:
            doc = parse_xml(
                content,  # type: ignore[arg-type]
                self._pullpoint_service.transport,
                settings=_DEFAULT_SETTINGS,
            )
        except XMLSyntaxError as exc:
            LOGGER.error("Received invalid XML: %s", exc)
            return

        async_operation_proxy = self._pullpoint_service.ws_client.PullMessages
        op_name = async_operation_proxy._op_name  # pylint: disable=protected-access
        binding = (
            async_operation_proxy._proxy._binding  # pylint: disable=protected-access
        )
        operation = binding.get(op_name)
        result = operation.process_reply(doc)
        LOGGER.debug(
            "Received webhook %s: %s: %s: %s", webhook_id, content, doc, result
        )
        await self.async_parse_messages(result.NotificationMessage)
        # Update entities
        for update_callback in self._listeners:
            update_callback()

    async def async_stop(self) -> None:
        """Unsubscribe from events."""
        self._listeners = []
        self.started = False
        self.async_unregister_webhook()

        if self._subscription:
            with suppress(*UNSUBSCRIBE_ERRORS):
                await self._subscription.Unsubscribe()
            self._subscription = None
        if self._webhook_subscription:
            with suppress(*UNSUBSCRIBE_ERRORS):
                await self._webhook_subscription.Unsubscribe()
            self._webhook_subscription = None

    async def async_restart(self, _now: dt.datetime | None = None) -> None:
        """Restart the subscription assuming the camera rebooted."""
        if not self.started:
            return

        if self._subscription:
            # Suppressed. The subscription may no longer exist.
            try:
                await self._subscription.Unsubscribe()
            except UNSUBSCRIBE_ERRORS as err:
                LOGGER.debug(
                    (
                        "Failed to unsubscribe ONVIF PullPoint subscription for '%s';"
                        " This is normal if the device restarted: %s"
                    ),
                    self.unique_id,
                    err,
                )
            self._subscription = None

        if self._webhook_subscription:
            # Suppressed. The subscription may no longer exist.
            try:
                await self._webhook_subscription.Unsubscribe()
            except UNSUBSCRIBE_ERRORS as err:
                LOGGER.debug(
                    (
                        "Failed to unsubscribe ONVIF webhook subscription for '%s';"
                        " This is normal if the device restarted: %s"
                    ),
                    self.unique_id,
                    err,
                )
            self._webhook_subscription = None

        try:
            restarted = await self.async_start()
        except (XMLParseError, *SUBSCRIPTION_ERRORS) as err:
            restarted = False
            # Device may not support subscriptions so log at debug level
            # when we get an XMLParseError
            LOGGER.log(
                DEBUG if isinstance(err, XMLParseError) else WARNING,
                (
                    "Failed to restart ONVIF PullPoint subscription for '%s'; "
                    "Retrying later: %s"
                ),
                self.unique_id,
                _stringify_onvif_error(err),
            )

        if not restarted:
            # Try again in a minute
            self._unsub_refresh = async_call_later(self.hass, 60, self.async_restart)
        elif self._listeners:
            LOGGER.debug(
                "Restarted ONVIF PullPoint subscription for '%s'", self.unique_id
            )
            self.async_schedule_pull()

    async def async_renew(self) -> None:
        """Renew subscription."""
        if self._subscription:
            try:
                # The first time we renew, we may get a Fault error so we
                # suppress it. The subscription will be restarted in
                # async_restart later.
                await self._subscription.Renew(_get_next_termination_time())
            except SUBSCRIPTION_ERRORS as err:
                LOGGER.debug(
                    "Failed to renew ONVIF PullPoint subscription for '%s'; %s",
                    self.unique_id,
                    err,
                )

        # TODO: know when to renew the notify service
        if self._webhook_subscription:
            try:
                await self._webhook_subscription.Renew(_get_next_termination_time())
            except SUBSCRIPTION_ERRORS as err:
                LOGGER.debug(
                    "Failed to renew ONVIF webhook subscription for '%s'; %s",
                    self.unique_id,
                    err,
                )

    def async_schedule_pull(self) -> None:
        """Schedule async_pull_messages to run."""
        self._unsub_refresh = async_call_later(self.hass, 1, self.async_pull_messages)

    async def async_pull_messages(self, _now: dt.datetime | None = None) -> None:
        """Pull messages from device."""
        if self.hass.state == CoreState.running:
            try:
                pullpoint = self.device.create_pullpoint_service()
                response = await pullpoint.PullMessages(
                    {"MessageLimit": 100, "Timeout": dt.timedelta(seconds=60)}
                )
                LOGGER.warning("Pulled messages: %s - %s", response, type(response))

                # Renew subscription if less than two hours is left
                if (
                    dt_util.as_utc(response.TerminationTime) - dt_util.utcnow()
                ).total_seconds() < 7200:
                    await self.async_renew()
            except RemoteProtocolError:
                # Likely a shutdown event, nothing to see here
                return
            except (XMLParseError, *SUBSCRIPTION_ERRORS) as err:
                # Device may not support subscriptions so log at debug level
                # when we get an XMLParseError
                LOGGER.log(
                    DEBUG if isinstance(err, XMLParseError) else WARNING,
                    (
                        "Failed to fetch ONVIF PullPoint subscription messages for"
                        " '%s': %s"
                    ),
                    self.unique_id,
                    _stringify_onvif_error(err),
                )
                # Treat errors as if the camera restarted. Assume that the pullpoint
                # subscription is no longer valid.
                self._unsub_refresh = None
                await self.async_restart()
                return

            # Parse response
            await self.async_parse_messages(response.NotificationMessage)

            # Update entities
            for update_callback in self._listeners:
                update_callback()

        # Reschedule another pull
        if self._listeners:
            self.async_schedule_pull()

    # pylint: disable=protected-access
    async def async_parse_messages(self, messages) -> None:
        """Parse notification message."""
        for msg in messages:
            # Guard against empty message
            if not msg.Topic:
                continue

            topic = msg.Topic._value_1
            if not (parser := PARSERS.get(topic)):
                if topic not in UNHANDLED_TOPICS:
                    LOGGER.info(
                        "No registered handler for event from %s: %s",
                        self.unique_id,
                        msg,
                    )
                    UNHANDLED_TOPICS.add(topic)
                continue

            event = await parser(self.unique_id, msg)

            if not event:
                LOGGER.info("Unable to parse event from %s: %s", self.unique_id, msg)
                return

            self._events[event.uid] = event

    def get_uid(self, uid) -> Event | None:
        """Retrieve event for given id."""
        return self._events.get(uid)

    def get_platform(self, platform) -> list[Event]:
        """Retrieve events for given platform."""
        return [event for event in self._events.values() if event.platform == platform]
