"""Config flow for eufy_sdk — connect to the bridge, then drive 2FA/captcha to login."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    EufySdkApiClient,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)
from .const import (
    CONF_GO2RTC_RTSP_PORT,
    CONF_HOST,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_GO2RTC_RTSP_PORT,
    DEFAULT_POLL_INTERVAL_MIN,
    DEFAULT_PORT,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo


class EufySdkFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Ask for the bridge address, then walk the user through login (2FA / captcha)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> EufySdkOptionsFlow:
        """Return the options flow (poll interval)."""
        return EufySdkOptionsFlow()

    def __init__(self) -> None:
        """Hold the in-flight bridge connection across steps."""
        self._client: EufySdkApiClient | None = None
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._go2rtc_rtsp_port: int = DEFAULT_GO2RTC_RTSP_PORT
        self._entry_to_update: config_entries.ConfigEntry | None = None

    def _set_bridge_fields(self, data: dict[str, Any]) -> None:
        """Store bridge form/discovery fields on the in-progress flow."""
        self._host = str(data[CONF_HOST]).strip()
        # NumberSelector hands back a float (3012.0); int-ify it for URLs.
        self._port = int(data[CONF_PORT])
        self._go2rtc_rtsp_port = int(
            data.get(CONF_GO2RTC_RTSP_PORT, DEFAULT_GO2RTC_RTSP_PORT)
        )

    def _bridge_schema(self, defaults: dict[str, Any]) -> vol.Schema:
        """Return the shared bridge address form schema."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=defaults.get(CONF_HOST, vol.UNDEFINED),
                ): selector.TextSelector(),
                vol.Required(
                    CONF_PORT,
                    default=defaults.get(CONF_PORT, DEFAULT_PORT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=65535, mode=selector.NumberSelectorMode.BOX
                    ),
                ),
                vol.Required(
                    CONF_GO2RTC_RTSP_PORT,
                    default=defaults.get(
                        CONF_GO2RTC_RTSP_PORT, DEFAULT_GO2RTC_RTSP_PORT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=65535, mode=selector.NumberSelectorMode.BOX
                    ),
                ),
            }
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """
        Re-drive login when the bridge loses auth after setup.

        The coordinator raises `ConfigEntryAuthFailed` when the bridge auth is non-`ok`
        (session expired → a fresh 2FA/captcha is needed). HA starts a reauth flow and
        calls this; we reconnect and reuse the `twofa` / `captcha` steps so the user can
        complete the challenge from inside HA.
        """
        self._host = entry_data[CONF_HOST]
        self._port = int(entry_data[CONF_PORT])
        self._go2rtc_rtsp_port = int(
            entry_data.get(CONF_GO2RTC_RTSP_PORT, DEFAULT_GO2RTC_RTSP_PORT)
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 — a bare confirm, no fields
    ) -> config_entries.ConfigFlowResult:
        """Reconnect to the bridge, then route to its current auth challenge."""
        try:
            self._client = EufySdkApiClient(
                self._host, self._port, async_get_clientsession(self.hass)
            )
            await self._client.connect()
        except EufySdkApiClientCommunicationError as err:
            LOGGER.warning("bridge reconnect for reauth failed: %s", err)
            return self.async_show_form(
                step_id="reauth_confirm",
                errors={"base": "cannot_connect"},
                description_placeholders={"host": self._host},
            )
        return await self._continue_auth()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Step 1: the bridge's host + port."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._set_bridge_fields(user_input)
            await self.async_set_unique_id(f"{self._host}:{self._port}")
            self._abort_if_unique_id_configured()
            try:
                self._client = EufySdkApiClient(
                    self._host, self._port, async_get_clientsession(self.hass)
                )
                await self._client.connect()
            except EufySdkApiClientCommunicationError as err:
                LOGGER.warning("bridge connect failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._continue_auth()

        return self.async_show_form(
            step_id="user",
            data_schema=self._bridge_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Update the bridge API port or the published RTSP port."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            self._set_bridge_fields(user_input)
            await self.async_set_unique_id(entry.unique_id)
            self._abort_if_unique_id_mismatch()
            try:
                self._client = EufySdkApiClient(
                    self._host, self._port, async_get_clientsession(self.hass)
                )
                await self._client.connect()
                await self._client.close()
            except EufySdkApiClientCommunicationError as err:
                LOGGER.warning("bridge reconnect for reconfigure failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=self._entry_data()
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._bridge_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_hassio(
        self,
        discovery_info: HassioServiceInfo,
    ) -> config_entries.ConfigFlowResult:
        """Handle Home Assistant Supervisor add-on discovery."""
        config = discovery_info.config
        try:
            self._host = str(config[CONF_HOST]).strip()
            self._port = int(config[CONF_PORT])
            self._go2rtc_rtsp_port = int(
                config.get(CONF_GO2RTC_RTSP_PORT)
                or config.get("rtsp_port")
                or DEFAULT_GO2RTC_RTSP_PORT
            )
        except (KeyError, TypeError, ValueError):
            return self.async_abort(reason="invalid_discovery_info")

        if not self._host:
            return self.async_abort(reason="invalid_discovery_info")

        self.context["title_placeholders"] = {
            "name": discovery_info.name,
            "host": f"{self._host}:{self._port}",
        }

        self._entry_to_update = self._find_existing_entry(discovery_info.uuid)
        if self._entry_to_update is None:
            await self.async_set_unique_id(discovery_info.uuid)
            self._abort_if_unique_id_configured(updates=self._entry_data())

        try:
            self._client = EufySdkApiClient(
                self._host, self._port, async_get_clientsession(self.hass)
            )
            await self._client.connect()
        except EufySdkApiClientCommunicationError as err:
            LOGGER.warning("discovered bridge connect failed: %s", err)
            return self.async_abort(reason="cannot_connect")

        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Confirm setup from add-on discovery before creating/updating an entry."""
        if user_input is not None:
            return await self._continue_auth()

        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={
                "host": f"{self._host}:{self._port}",
                "rtsp": f"{self._host}:{self._go2rtc_rtsp_port}",
            },
        )

    async def _continue_auth(self) -> config_entries.ConfigFlowResult:
        """Route to the right step for the bridge's current auth state."""
        if self._client is None:
            return self.async_abort(reason="cannot_connect")
        try:
            auth = await self._client.auth_status()
        except EufySdkApiClientError as err:
            LOGGER.error("auth.status failed: %s", err)
            await self._client.close()
            self._client = None
            return self.async_abort(reason="cannot_connect")

        state = auth.get("state")
        if state == "ok":
            return await self._complete_auth()
        if state == "require_2fa":
            return await self.async_step_twofa()
        if state == "require_captcha":
            return await self.async_step_captcha()
        # "pending": ask the bridge for a challenge, then re-route.
        await self._client.retrigger_auth()
        return await self._continue_auth()

    async def _complete_auth(self) -> config_entries.ConfigFlowResult:
        """Close the setup client and create or update the config entry."""
        if self._client is not None:
            await self._client.close()  # the coordinator opens its own connection
            self._client = None
        if self.source == config_entries.SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates={}
            )
        if self._entry_to_update is not None:
            return self.async_update_reload_and_abort(
                self._entry_to_update, data_updates=self._entry_data()
            )
        return self.async_create_entry(
            title=f"eufy bridge ({self._host})",
            data=self._entry_data(),
        )

    def _entry_data(self) -> dict[str, Any]:
        """Return the config-entry data for the current bridge/go2rtc addresses."""
        return {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_GO2RTC_RTSP_PORT: self._go2rtc_rtsp_port,
        }

    def _find_existing_entry(
        self, discovery_uuid: str
    ) -> config_entries.ConfigEntry | None:
        """Find an existing add-on/manual entry that represents this bridge."""
        for entry in self._async_current_entries():
            if entry.unique_id == discovery_uuid:
                return entry
            try:
                same_bridge = (
                    entry.data.get(CONF_HOST) == self._host
                    and int(entry.data[CONF_PORT]) == self._port
                )
            except (KeyError, TypeError, ValueError):
                same_bridge = False
            if same_bridge:
                return entry
        return None

    async def async_step_twofa(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Submit the 2FA code that was sent to the account."""
        if self._client is None:
            return self.async_abort(reason="cannot_connect")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("resend"):
                await self._client.retrigger_auth()
                return await self._continue_auth()
            auth = await self._client.submit_2fa(str(user_input["code"]))
            if auth.get("state") == "ok":
                return await self._continue_auth()
            errors["base"] = "invalid_2fa"

        return self.async_show_form(
            step_id="twofa",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): selector.TextSelector(),
                    vol.Optional("resend", default=False): selector.BooleanSelector(),
                },
            ),
            errors=errors,
        )

    async def async_step_captcha(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Show the captcha image (markdown in the description) and take the answer."""
        if self._client is None:
            return self.async_abort(reason="cannot_connect")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("refresh"):
                await self._client.retrigger_auth()
                return await self._continue_auth()
            auth = await self._client.submit_captcha(str(user_input["answer"]))
            if auth.get("state") == "ok":
                return await self._continue_auth()
            errors["base"] = "invalid_captcha"

        auth = await self._client.auth_status()
        image = auth.get("image", "")
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema(
                {
                    vol.Required("answer"): selector.TextSelector(),
                    vol.Optional("refresh", default=False): selector.BooleanSelector(),
                },
            ),
            # The frontend renders the description as markdown; embed the data-URI.
            description_placeholders={
                "image": f"![captcha]({image})"
                if image
                else "(captcha unavailable — tick refresh)"
            },
            errors=errors,
        )


class EufySdkOptionsFlow(config_entries.OptionsFlowWithReload):
    """Options for runtime tuning."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Show and save the poll interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current_poll_interval = self.config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_MIN
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL, default=current_poll_interval
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=1440,
                            step=1,
                            unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                },
            ),
        )
