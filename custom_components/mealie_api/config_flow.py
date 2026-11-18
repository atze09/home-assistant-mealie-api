"""Config flow for the Mealie API integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MealieApiError, MealieAuthError, MealieClient
from .const import CONF_GROUP_SLUG, DOMAIN

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_TOKEN): str,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)


class MealieApiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mealie API."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for URL and token and check them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            session = async_get_clientsession(self.hass, user_input[CONF_VERIFY_SSL])
            client = MealieClient(session, url, user_input[CONF_API_TOKEN].strip())
            try:
                user = await client.whoami()
            except MealieAuthError:
                errors["base"] = "invalid_auth"
            except MealieApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{url}|{user.get('id')}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Mealie API ({user.get('username') or user.get('fullName') or url})",
                    data={
                        CONF_URL: url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        CONF_GROUP_SLUG: user.get("groupSlug"),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER_SCHEMA, user_input),
            errors=errors,
        )
