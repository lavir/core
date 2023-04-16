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

from .const import DOMAIN, LOGGER
from .models import Event
from .parsers import PARSERS

UNHANDLED_TOPICS: set[str] = set()

SUBSCRIPTION_ERRORS = (Fault, asyncio.TimeoutError, TransportError)
SET_SYNCHRONIZATION_POINT_ERRORS = (*SUBSCRIPTION_ERRORS, TypeError)
UNSUBSCRIBE_ERRORS = (XMLParseError, *SUBSCRIPTION_ERRORS)


SUBSCRIPTION_TIME = dt.timedelta(minutes=3)
SUBSCRIPTION_RELATIVE_TIME = (
    "PT3M"  # use relative time since the time on the camera is not reliable
)
SUBSCRIPTION_RENEW_INTERVAL = SUBSCRIPTION_TIME.total_seconds() / 2
SUBSCRIPTION_RENEW_INTERVAL_ON_ERROR = 60

PULLPOINT_POLL_TIME = dt.timedelta(seconds=60)
PULLPOINT_INIT_POLL_TIME = dt.timedelta(seconds=5)
PULLPOINT_MESSAGE_LIMIT = 100


def _get_next_termination_time() -> str:
    """Get next termination time."""
    return SUBSCRIPTION_RELATIVE_TIME


def _stringify_onvif_error(error: Exception) -> str:
    """Stringify ONVIF error."""
    if isinstance(error, Fault):
        return error.message or str(error) or "Device sent empty error"
    return str(error)


class EventManager:
    """ONVIF Event Manager."""

    def __init__(
        self, hass: HomeAssistant, device: ONVIFCamera, unique_id: str, name: str
    ) -> None:
        """Initialize event manager."""
        self.hass = hass
        self.device = device
        self.unique_id = unique_id
        self.name = name

        self.webhook_manager = WebHookManager(self)
        self.pullpoint_manager = PullPointManager(self)
        self.webhook_is_reachable: bool = False

        self._events: dict[str, Event] = {}
        self._listeners: list[CALLBACK_TYPE] = []

    @property
    def started(self) -> bool:
        """Return True if event manager is started."""
        return self.webhook_manager.started or self.pullpoint_manager.started

    @property
    def platforms(self) -> set[str]:
        """Return platforms to setup."""
        return {event.platform for event in self._events.values()}

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> Callable[[], None]:
        """Listen for data updates."""
        # This is the first listener, set up polling.
        if not self._listeners and not self.webhook_is_reachable:
            self.pullpoint_manager.async_schedule_pull()

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

        if not self._listeners:
            self.pullpoint_manager.async_cancel_pull_messages()

    async def async_start(self) -> bool:
        """Start polling events."""
        if self.webhook_manager.started or self.pullpoint_manager.started:
            raise RuntimeError("Event manager already started")
        # Always start pull point first, since it will populate the event list
        event_via_pull_point = await self.pullpoint_manager.async_start()
        events_via_webhook = await self.webhook_manager.async_start()
        return events_via_webhook or event_via_pull_point

    async def async_stop(self) -> None:
        """Unsubscribe from events."""
        self._listeners = []
        await self.pullpoint_manager.async_stop()
        await self.webhook_manager.async_stop()

    @callback
    def async_callback_listeners(self) -> None:
        """Update listeners."""
        for update_callback in self._listeners:
            update_callback()

    @property
    def has_listeners(self) -> bool:
        """Return if there are listeners."""
        return bool(self._listeners)

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


class PullPointManager:
    """ONVIF PullPoint Manager.

    If the camera supports webhooks and the webhook is reachable, the pullpoint
    manager will keep the pull point subscription alive, but will not poll for
    messages unless the webhook fails.
    """

    def __init__(self, event_manager: EventManager) -> None:
        """Initialize pullpoint manager."""
        self.started: bool = False

        self._event_manager = event_manager
        self._device = event_manager.device
        self._hass = event_manager.hass
        self._unique_id = event_manager.unique_id
        self._name = event_manager.name

        self._pullpoint_subscription: ONVIFService = None
        self._pullpoint_service: ONVIFService = None
        self._pull_lock: asyncio.Lock = asyncio.Lock()

        self._cancel_pull_messages: CALLBACK_TYPE | None = None
        self._cancel_pullpoint_renew: CALLBACK_TYPE | None = None

    async def async_start(self) -> bool:
        """Start pullpoint subscription."""
        assert self.started is False, "PullPoint manager already started"
        LOGGER.debug("%s: Starting PullPoint manager", self._name)
        self.started = await self._async_start_pullpoint()
        return self.started

    async def _async_start_pullpoint(self) -> bool:
        """Start pullpoint subscription."""
        try:
            started = await self._async_create_pullpoint_subscription()
        except (ONVIFError, Fault, RequestError, XMLParseError) as err:
            LOGGER.debug(
                "%s: Device does not support PullPoint service or has too many subscriptions: %s",
                self._name,
                _stringify_onvif_error(err),
            )
            return False
        if started:
            self._async_schedule_pullpoint_renew()
        return started

    @callback
    def _async_schedule_pullpoint_renew(self) -> None:
        """Schedule PullPoint subscription renewal."""
        self._async_cancel_pullpoint_renew()
        self._cancel_pullpoint_renew = async_call_later(
            self._hass,
            SUBSCRIPTION_RENEW_INTERVAL,
            self._async_renew_or_restart_pullpoint,
        )

    @callback
    def async_cancel_pull_messages(self) -> None:
        """Cancel the PullPoint task."""
        if self._cancel_pull_messages:
            self._cancel_pull_messages()
            self._cancel_pull_messages = None

    @callback
    def async_schedule_pull(self) -> None:
        """Schedule async_pull_messages to run.

        Used as fallback when webhook is not reachable.

        Must not check if the webhook is reachable.
        """
        self.async_cancel_pull_messages()
        if self._pullpoint_service:
            self._cancel_pull_messages = async_call_later(
                self._hass, 1, self._async_pull_messages
            )

    async def async_stop(self) -> None:
        """Unsubscribe from PullPoint and cancel callbacks."""
        self.started = False
        self._async_cancel_pullpoint_renew()
        self.async_cancel_pull_messages()
        await self._async_unsubscribe_pullpoint()

    async def _async_renew_or_restart_pullpoint(
        self, now: dt.datetime | None = None
    ) -> None:
        """Renew or start pullpoint subscription."""
        if self._hass.is_stopping or not self.started:
            return
        next_attempt = SUBSCRIPTION_RENEW_INTERVAL
        try:
            if (
                not await self._async_renew_pullpoint()
                and not await self._async_restart_pullpoint()
            ):
                next_attempt = SUBSCRIPTION_RENEW_INTERVAL_ON_ERROR
        finally:
            self._cancel_pullpoint_renew = async_call_later(
                self._hass,
                next_attempt,
                self._async_renew_or_restart_pullpoint,
            )

    async def _async_create_pullpoint_subscription(self) -> bool:
        """Create pullpoint subscription."""
        if not await self._device.create_pullpoint_subscription(
            {"InitialTerminationTime": _get_next_termination_time()}
        ):
            LOGGER.debug("%s: Failed to create PullPoint subscription", self._name)
            return False

        # Create subscription manager
        self._pullpoint_subscription = self._device.create_subscription_service(
            "PullPointSubscription"
        )

        # Create the service that will be used to pull messages from the device.
        self._pullpoint_service = self._device.create_pullpoint_service()

        # Initialize events
        with suppress(*SET_SYNCHRONIZATION_POINT_ERRORS):
            sync_result = await self._pullpoint_service.SetSynchronizationPoint()
            LOGGER.debug("%s: SetSynchronizationPoint: %s", self._name, sync_result)

        if response := await self._pullpoint_service.PullMessages(
            {
                "MessageLimit": PULLPOINT_MESSAGE_LIMIT,
                "Timeout": PULLPOINT_INIT_POLL_TIME,
            }
        ):
            LOGGER.debug("%s: PullMessages: %s", self._name, response)
            # Parse event initialization
            await self._event_manager.async_parse_messages(response.NotificationMessage)
            self._event_manager.async_callback_listeners()

        if (
            self._event_manager.has_listeners
            and not self._event_manager.webhook_is_reachable
        ):
            self.async_schedule_pull()

        return True

    @callback
    def _async_cancel_pullpoint_renew(self) -> None:
        """Cancel the pullpoint renew task."""
        if self._cancel_pullpoint_renew:
            self._cancel_pullpoint_renew()
            self._cancel_pullpoint_renew = None

    async def _async_restart_pullpoint(self) -> bool:
        """Restart the subscription assuming the camera rebooted."""
        await self._async_unsubscribe_pullpoint()
        restarted = await self._async_start_pullpoint()
        if restarted and self._event_manager.has_listeners:
            LOGGER.debug("Restarted ONVIF PullPoint subscription for '%s'", self._name)
            self.async_schedule_pull()
        return restarted

    async def _async_unsubscribe_pullpoint(self) -> None:
        """Unsubscribe the pullpoint subscription."""
        if not self._pullpoint_subscription:
            return
        # Suppressed. The subscription may no longer exist.
        try:
            await self._pullpoint_subscription.Unsubscribe()
        except UNSUBSCRIBE_ERRORS as err:
            LOGGER.debug(
                (
                    "Failed to unsubscribe ONVIF PullPoint subscription for '%s';"
                    " This is normal if the device restarted: %s"
                ),
                self._name,
                _stringify_onvif_error(err),
            )
        self._pullpoint_subscription = None

    async def _async_renew_pullpoint(self) -> bool:
        """Renew the PullPoint subscription."""
        if not self._pullpoint_subscription:
            return False
        try:
            # The first time we renew, we may get a Fault error so we
            # suppress it. The subscription will be restarted in
            # async_restart later.
            await self._pullpoint_subscription.Renew(_get_next_termination_time())
            LOGGER.debug("Renewed ONVIF PullPoint subscription for '%s'", self._name)
            return True
        except SUBSCRIPTION_ERRORS as err:
            LOGGER.debug(
                "Failed to renew ONVIF PullPoint subscription for '%s'; %s",
                self._name,
                _stringify_onvif_error(err),
            )
        return False

    async def _async_pull_messages_or_try_to_restart(self) -> None:
        """Pull messages from device or try to restart the subscription.

        This function must not be called directly, it should only
        be called from _async_pull_messages
        """
        assert self._pullpoint_service is not None, "PullPoint service does not exist"
        LOGGER.debug("%s: Pulling ONVIF PullPoint messages", self._name)
        try:
            response = await self._pullpoint_service.PullMessages(
                {
                    "MessageLimit": PULLPOINT_MESSAGE_LIMIT,
                    "Timeout": PULLPOINT_POLL_TIME,
                }
            )
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
                self._event_manager.unique_id,
                _stringify_onvif_error(err),
            )
            # Treat errors as if the camera restarted. Assume that the pullpoint
            # subscription is no longer valid.
            self._event_manager.webhook_is_reachable = False
            self._async_cancel_pullpoint_renew()
            await self._async_renew_or_restart_pullpoint()
            return

        # Parse response
        await self._event_manager.async_parse_messages(response.NotificationMessage)
        self._event_manager.async_callback_listeners()

    async def _async_pull_messages(self, _now: dt.datetime | None = None) -> None:
        """Pull messages from device."""
        self._cancel_pull_messages = None
        if self._hass.state == CoreState.running and not self._pull_lock.locked():
            # Pull messages if the lock is not already locked
            # any pull will do, so we don't need to wait for the lock
            async with self._pull_lock:
                await self._async_pull_messages_or_try_to_restart()
        if (
            self._event_manager.has_listeners
            and not self._event_manager.webhook_is_reachable
        ):
            self.async_schedule_pull()


class WebHookManager:
    """Manage ONVIF webhook subscriptions.

    If the camera supports webhooks, we will use that instead of
    pullpoint subscriptions as soon as we detect that the camera
    can reach our webhook.
    """

    def __init__(self, event_manager: EventManager) -> None:
        """Initialize webhook manager."""
        self.started: bool = False

        self._event_manager = event_manager
        self._device = event_manager.device
        self._hass = event_manager.hass
        self._unique_id = event_manager.unique_id
        self._name = event_manager.name

        self._webhook_subscription: ONVIFService = None
        self._webhook_pullpoint_service: ONVIFService = None

        self._webhook_id: str | None = None
        self._base_url: str | None = None
        self._webhook_url: str | None = None
        self._notify_service: ONVIFService | None = None

        self._cancel_webhook_renew: CALLBACK_TYPE | None = None

    async def async_start(self) -> bool:
        """Start polling events."""
        LOGGER.debug("%s: Starting webhook manager", self._name)
        assert self.started is False, "Webhook manager already started"
        assert self._webhook_id is None, "Webhook already registered"
        self._async_register_webhook()
        self.started = await self._async_start_webhook()
        return self.started

    async def async_stop(self) -> None:
        """Unsubscribe from events."""
        self.started = False
        self._async_cancel_webhook_renew()
        await self._async_unsubscribe_webhook()
        self._async_unregister_webhook()

    @callback
    def _async_schedule_webhook_renew(self) -> None:
        """Schedule webhook subscription renewal."""
        self._async_cancel_webhook_renew()
        self._cancel_webhook_renew = async_call_later(
            self._hass,
            SUBSCRIPTION_RENEW_INTERVAL,
            self._async_renew_or_restart_webhook,
        )

    async def _async_create_webhook_subscription(self) -> None:
        """Create webhook subscription."""
        self._notify_service = self._device.create_notification_service()
        notify_subscribe = await self._notify_service.Subscribe(
            {
                "InitialTerminationTime": _get_next_termination_time(),
                "ConsumerReference": {"Address": self._webhook_url},
            }
        )
        # pylint: disable=protected-access
        self._device.xaddrs[
            "http://www.onvif.org/ver10/events/wsdl/WebhookSubscription"
        ] = notify_subscribe.SubscriptionReference.Address._value_1

        # Create subscription manager
        self._webhook_subscription = self._device.create_subscription_service(
            "WebhookSubscription"
        )
        self._webhook_pullpoint_service = self._device.create_onvif_service(
            "pullpoint", port_type="WebhookSubscription"
        )

        # 5.2.3 BASIC NOTIFICATION INTERFACE - NOTIFY
        # Call SetSynchronizationPoint to generate a notification message
        # to ensure the webhooks are working.
        try:
            await self._webhook_pullpoint_service.SetSynchronizationPoint()
        except SET_SYNCHRONIZATION_POINT_ERRORS:
            LOGGER.debug("%s: SetSynchronizationPoint failed", self._name)

        LOGGER.debug("%s: Webhook subscription created", self._name)

    async def _async_start_webhook(self) -> bool:
        """Start webhook."""
        try:
            await self._async_create_webhook_subscription()
        except (ONVIFError, Fault, RequestError, XMLParseError) as err:
            # Do not unregister the webhook because if its still
            # subscribed to events, it will still receive them.
            LOGGER.debug(
                "%s: Device does not support notification service or too many subscriptions: %s",
                self._name,
                _stringify_onvif_error(err),
            )
            return False

        self._async_schedule_webhook_renew()
        return True

    async def _async_restart_webhook(self) -> bool:
        """Restart the webhook subscription assuming the camera rebooted."""
        await self._async_unsubscribe_webhook()
        return await self._async_start_webhook()

    async def _async_renew_webhook(self) -> bool:
        """Renew webhook subscription."""
        try:
            await self._webhook_subscription.Renew(_get_next_termination_time())
            LOGGER.debug("%s: Webhook subscription renewed", self._name)
            return True
        except (ONVIFError, Fault, RequestError, XMLParseError) as err:
            LOGGER.debug(
                "%s: Failed to renew webhook subscription %s",
                self._name,
                _stringify_onvif_error(err),
            )
        return False

    async def _async_renew_or_restart_webhook(
        self, now: dt.datetime | None = None
    ) -> None:
        """Renew or start webhook subscription."""
        if self._hass.is_stopping or not self.started:
            return
        next_attempt = SUBSCRIPTION_RENEW_INTERVAL
        try:
            if (
                not await self._async_renew_webhook()
                and not await self._async_restart_webhook()
            ):
                next_attempt = SUBSCRIPTION_RENEW_INTERVAL_ON_ERROR
        finally:
            self._cancel_webhook_renew = async_call_later(
                self._hass,
                next_attempt,
                self._async_renew_or_restart_webhook,
            )

    @callback
    def _async_register_webhook(self) -> None:
        """Register the webhook for motion events."""
        webhook_id = f"{DOMAIN}_{self._unique_id}_events"
        self._webhook_id = webhook_id
        try:
            self._base_url = get_url(self._hass, prefer_external=False)
        except NoURLAvailableError:
            try:
                self._base_url = get_url(self._hass, prefer_external=True)
            except NoURLAvailableError:
                self._async_unregister_webhook()

        webhook.async_register(
            self._hass, DOMAIN, webhook_id, webhook_id, self._async_handle_webhook
        )
        webhook_path = webhook.async_generate_path(webhook_id)
        self._webhook_url = f"{self._base_url}{webhook_path}"

        LOGGER.debug("Registered webhook: %s", webhook_id)

    @callback
    def _async_unregister_webhook(self):
        """Unregister the webhook for motion events."""
        LOGGER.debug("Unregistering webhook %s", self._webhook_id)
        webhook.async_unregister(self._hass, self._webhook_id)
        self._webhook_id = None

    async def _async_handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> None:
        """Handle incoming webhook."""
        self._event_manager.webhook_is_reachable = True
        content: bytes | None = None
        try:
            content = await request.read()
        except ConnectionResetError as ex:
            LOGGER.error("Error reading webhook: %s", ex)
            return
        except asyncio.CancelledError as ex:
            LOGGER.error("Error reading webhook: %s", ex)
            raise
        finally:
            self._hass.async_create_background_task(
                self._async_process_webhook(hass, webhook_id, content),
                f"ONVIF event webhook for {self._name}",
            )

    async def _async_process_webhook(
        self, hass: HomeAssistant, webhook_id: str, content: bytes | None
    ) -> None:
        """Process incoming webhook data in the background."""
        if content is None:
            self._event_manager.pullpoint_manager.async_schedule_pull()
            return

        assert self._webhook_pullpoint_service is not None
        assert self._webhook_pullpoint_service.transport is not None
        try:
            doc = parse_xml(
                content,  # type: ignore[arg-type]
                self._webhook_pullpoint_service.transport,
                settings=_DEFAULT_SETTINGS,
            )
        except XMLSyntaxError as exc:
            LOGGER.error("Received invalid XML: %s", exc)
            return

        async_operation_proxy = self._webhook_pullpoint_service.ws_client.PullMessages
        op_name = async_operation_proxy._op_name  # pylint: disable=protected-access
        binding = (
            async_operation_proxy._proxy._binding  # pylint: disable=protected-access
        )
        operation = binding.get(op_name)
        result = operation.process_reply(doc)
        LOGGER.debug(
            "Processed webhook %s: %s: %s: %s", webhook_id, content, doc, result
        )
        await self._event_manager.async_parse_messages(result.NotificationMessage)
        self._event_manager.async_callback_listeners()

    @callback
    def _async_cancel_webhook_renew(self) -> None:
        """Cancel the webhook renew task."""
        if self._cancel_webhook_renew:
            self._cancel_webhook_renew()
            self._cancel_webhook_renew = None

    async def _async_unsubscribe_webhook(self) -> None:
        """Unsubscribe from the webhook."""
        if not self._webhook_subscription:
            return
        # Suppressed. The subscription may no longer exist.
        try:
            await self._webhook_subscription.Unsubscribe()
        except UNSUBSCRIBE_ERRORS as err:
            LOGGER.debug(
                (
                    "Failed to unsubscribe ONVIF webhook subscription for '%s';"
                    " This is normal if the device restarted: %s"
                ),
                self._name,
                _stringify_onvif_error(err),
            )
        self._webhook_subscription = None
