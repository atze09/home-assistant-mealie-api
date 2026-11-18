"""Mealie API: exposes Mealie's REST API (create/edit recipes, raw requests) as actions."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import MealieApiError, MealieAuthError, MealieClient
from .const import (
    ATTR_CATEGORIES,
    ATTR_COOK_TIME,
    ATTR_DATA,
    ATTR_DESCRIPTION,
    ATTR_IMAGE_PATH,
    ATTR_IMAGE_URL,
    ATTR_INCLUDE_TAGS,
    ATTR_INGREDIENTS,
    ATTR_INSTRUCTIONS,
    ATTR_METHOD,
    ATTR_NAME,
    ATTR_NOTES,
    ATTR_PARAMS,
    ATTR_PATH,
    ATTR_PREP_TIME,
    ATTR_RECIPE,
    ATTR_RECIPE_YIELD,
    ATTR_SOURCE_URL,
    ATTR_TAGS,
    ATTR_TOTAL_TIME,
    CONF_CONFIG_ENTRY_ID,
    CONF_GROUP_SLUG,
    DOMAIN,
    SERVICE_API_REQUEST,
    SERVICE_CREATE_FROM_JSON,
    SERVICE_CREATE_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_SET_IMAGE,
    SERVICE_UPDATE_RECIPE,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type MealieApiConfigEntry = ConfigEntry[MealieClient]


def _lines(value: Any) -> list[str]:
    """Accept a list or a multi-line string; return non-empty stripped lines."""
    if value is None:
        return []
    if isinstance(value, str):
        items = value.splitlines()
    else:
        items = [str(v) for v in cv.ensure_list(value)]
    return [item.strip() for item in items if item and item.strip()]


_TIME = vol.Any(cv.positive_int, cv.string)

_RECIPE_FIELDS = {
    vol.Optional(ATTR_DESCRIPTION): cv.string,
    vol.Optional(ATTR_INGREDIENTS): _lines,
    vol.Optional(ATTR_INSTRUCTIONS): _lines,
    vol.Optional(ATTR_NOTES): _lines,
    vol.Optional(ATTR_RECIPE_YIELD): cv.string,
    vol.Optional(ATTR_PREP_TIME): _TIME,
    vol.Optional(ATTR_COOK_TIME): _TIME,
    vol.Optional(ATTR_TOTAL_TIME): _TIME,
    vol.Optional(ATTR_TAGS): _lines,
    vol.Optional(ATTR_CATEGORIES): _lines,
    vol.Optional(ATTR_SOURCE_URL): cv.string,
}
_ENTRY = {vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string}

CREATE_SCHEMA = vol.Schema({**_ENTRY, vol.Required(ATTR_NAME): cv.string, **_RECIPE_FIELDS})
UPDATE_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required(ATTR_RECIPE): cv.string, vol.Optional(ATTR_NAME): cv.string, **_RECIPE_FIELDS}
)
DELETE_SCHEMA = vol.Schema({**_ENTRY, vol.Required(ATTR_RECIPE): cv.string})
FROM_JSON_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_DATA): vol.Any(cv.string, dict),
        vol.Optional(ATTR_INCLUDE_TAGS, default=True): cv.boolean,
    }
)
IMAGE_SCHEMA = vol.All(
    vol.Schema(
        {
            **_ENTRY,
            vol.Required(ATTR_RECIPE): cv.string,
            vol.Exclusive(ATTR_IMAGE_URL, "image"): cv.url,
            vol.Exclusive(ATTR_IMAGE_PATH, "image"): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_IMAGE_URL, ATTR_IMAGE_PATH),
)
API_REQUEST_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_METHOD): vol.All(
            cv.string, vol.Upper, vol.In(["GET", "POST", "PUT", "PATCH", "DELETE"])
        ),
        vol.Required(ATTR_PATH): vol.All(cv.string, vol.Match(r"^/?api/")),
        vol.Optional(ATTR_DATA): vol.Any(dict, list, cv.string),
        vol.Optional(ATTR_PARAMS): dict,
    }
)


def _client(hass: HomeAssistant, call: ServiceCall) -> tuple[MealieClient, str | None]:
    """Pick the config entry for a call."""
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(CONF_CONFIG_ENTRY_ID):
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError("Keine geladene Mealie-API-Instanz gefunden")
    if len(entries) > 1:
        raise ServiceValidationError("Mehrere Instanzen – bitte config_entry_id angeben")
    entry: MealieApiConfigEntry = entries[0]
    return entry.runtime_data, entry.data.get(CONF_GROUP_SLUG)


def _fields(call: ServiceCall) -> dict[str, Any]:
    return {
        k: v
        for k, v in call.data.items()
        if k not in (CONF_CONFIG_ENTRY_ID, ATTR_RECIPE)
    }


async def _result(client: MealieClient, slug: str, group: str | None) -> dict[str, Any]:
    recipe = await client.get_recipe(slug)
    return {
        "slug": slug,
        "id": recipe.get("id"),
        "name": recipe.get("name"),
        "url": client.recipe_url(slug, group),
        "ingredients": [
            i.get("display") or i.get("note") or i.get("originalText")
            for i in recipe.get("recipeIngredient") or []
        ],
        "instructions": [s.get("text") for s in recipe.get("recipeInstructions") or []],
    }


def _wrap(func):
    """Turn API errors into HomeAssistantError."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        try:
            return await func(call)
        except MealieApiError as err:
            raise HomeAssistantError(f"Mealie: {err}") from err

    return handler


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""

    @_wrap
    async def create_recipe(call: ServiceCall) -> ServiceResponse:
        client, group = _client(hass, call)
        slug = await client.create_recipe(_fields(call))
        return await _result(client, slug, group)

    @_wrap
    async def update_recipe(call: ServiceCall) -> ServiceResponse:
        client, group = _client(hass, call)
        slug = await client.update_recipe(call.data[ATTR_RECIPE], _fields(call))
        return await _result(client, slug, group)

    @_wrap
    async def delete_recipe(call: ServiceCall) -> ServiceResponse:
        client, _ = _client(hass, call)
        await client.delete_recipe(call.data[ATTR_RECIPE])
        return {"deleted": call.data[ATTR_RECIPE]}

    @_wrap
    async def create_from_json(call: ServiceCall) -> ServiceResponse:
        client, group = _client(hass, call)
        data = call.data[ATTR_DATA]
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False)
        slug = await client.create_from_json(data, call.data[ATTR_INCLUDE_TAGS])
        return await _result(client, slug, group)

    @_wrap
    async def set_recipe_image(call: ServiceCall) -> ServiceResponse:
        client, group = _client(hass, call)
        slug = call.data[ATTR_RECIPE]
        if url := call.data.get(ATTR_IMAGE_URL):
            await client.set_image_from_url(slug, url)
        else:
            path = call.data[ATTR_IMAGE_PATH]
            if not hass.config.is_allowed_path(path):
                raise ServiceValidationError(
                    f"{path} ist nicht in allowlist_external_dirs freigegeben"
                )
            file = Path(path)
            content = await hass.async_add_executor_job(file.read_bytes)
            ext = file.suffix.lstrip(".").lower() or (
                (mimetypes.guess_extension(mimetypes.guess_type(path)[0] or "") or ".jpg")[1:]
            )
            await client.upload_image(slug, content, ext)
        return {"slug": slug, "url": client.recipe_url(slug, group)}

    @_wrap
    async def api_request(call: ServiceCall) -> ServiceResponse:
        client, _ = _client(hass, call)
        path = "/" + call.data[ATTR_PATH].lstrip("/")
        result = await client.request(
            call.data[ATTR_METHOD],
            path,
            json_body=call.data.get(ATTR_DATA),
            params=call.data.get(ATTR_PARAMS),
        )
        return {"result": result}

    for name, handler, schema, support in (
        (SERVICE_CREATE_RECIPE, create_recipe, CREATE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_UPDATE_RECIPE, update_recipe, UPDATE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_DELETE_RECIPE, delete_recipe, DELETE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_CREATE_FROM_JSON, create_from_json, FROM_JSON_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_SET_IMAGE, set_recipe_image, IMAGE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_API_REQUEST, api_request, API_REQUEST_SCHEMA, SupportsResponse.ONLY),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=support)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MealieApiConfigEntry) -> bool:
    """Set up a Mealie API instance."""
    session = async_get_clientsession(hass, entry.data.get(CONF_VERIFY_SSL, True))
    client = MealieClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    try:
        await client.whoami()
    except MealieAuthError as err:
        raise ConfigEntryError(f"Token ungültig: {err}") from err
    except MealieApiError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = client
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MealieApiConfigEntry) -> bool:
    """Unload an instance."""
    return True
