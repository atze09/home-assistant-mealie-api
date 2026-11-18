"""Small async client for the Mealie REST API."""

from __future__ import annotations

import json
from typing import Any
import uuid

import aiohttp


class MealieApiError(Exception):
    """Error returned by the Mealie API."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MealieAuthError(MealieApiError):
    """Token rejected."""


def _duration(value: str | int | None) -> str | None:
    """Minutes (int or digit string) -> ISO 8601 duration, otherwise pass through."""
    if value is None or value == "":
        return None
    if isinstance(value, int) or str(value).strip().isdigit():
        return f"PT{int(value)}M"
    return str(value)


def build_schema_recipe(data: dict[str, Any]) -> dict[str, Any]:
    """Build a schema.org Recipe object that Mealie's HTML/JSON importer understands."""
    recipe: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": data["name"],
    }
    if data.get("description"):
        recipe["description"] = data["description"]
    if data.get("ingredients"):
        recipe["recipeIngredient"] = list(data["ingredients"])
    if data.get("instructions"):
        recipe["recipeInstructions"] = [
            {"@type": "HowToStep", "text": step} for step in data["instructions"]
        ]
    if data.get("recipe_yield"):
        recipe["recipeYield"] = str(data["recipe_yield"])
    for key, schema_key in (
        ("prep_time", "prepTime"),
        ("cook_time", "cookTime"),
        ("total_time", "totalTime"),
    ):
        if (value := _duration(data.get(key))) is not None:
            recipe[schema_key] = value
    if data.get("tags"):
        recipe["keywords"] = ", ".join(data["tags"])
    if data.get("categories"):
        recipe["recipeCategory"] = list(data["categories"])
    if data.get("source_url"):
        recipe["url"] = data["source_url"]
    return recipe


def build_patch(data: dict[str, Any]) -> dict[str, Any]:
    """Build a PATCH body for /api/recipes/{slug} from the service fields given."""
    patch: dict[str, Any] = {}
    if data.get("name"):
        patch["name"] = data["name"]
    if "description" in data:
        patch["description"] = data["description"] or ""
    if "recipe_yield" in data:
        patch["recipeYield"] = str(data["recipe_yield"] or "")
    for key, api_key in (
        ("prep_time", "prepTime"),
        ("cook_time", "performTime"),
        ("total_time", "totalTime"),
    ):
        if key in data:
            value = data[key]
            if isinstance(value, int) or (value and str(value).isdigit()):
                value = f"{int(value)} Minuten"
            patch[api_key] = value or ""
    if "source_url" in data:
        patch["orgURL"] = data["source_url"] or ""
    if "ingredients" in data:
        patch["recipeIngredient"] = [
            {
                "referenceId": str(uuid.uuid4()),
                "title": None,
                "note": line,
                "display": line,
                "originalText": line,
                "quantity": 0,
                "unit": None,
                "food": None,
            }
            for line in data["ingredients"]
        ]
    if "instructions" in data:
        patch["recipeInstructions"] = [
            {
                "id": str(uuid.uuid4()),
                "title": "",
                "summary": "",
                "text": step,
                "ingredientReferences": [],
            }
            for step in data["instructions"]
        ]
    if "notes" in data:
        patch["notes"] = [{"title": "", "text": note} for note in data["notes"]]
    return patch


class MealieClient:
    """Thin wrapper around the Mealie REST API."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str
    ) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self._token = token

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        form: aiohttp.FormData | None = None,
    ) -> Any:
        """Call the API and return parsed JSON (or text / None)."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method.upper(),
                url,
                headers=headers,
                json=json_body if form is None else None,
                data=form,
                params=params,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    raise MealieAuthError(f"{resp.status}: {text[:300]}", resp.status)
                if resp.status >= 400:
                    raise MealieApiError(f"{resp.status}: {text[:500]}", resp.status)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MealieApiError(f"Verbindung zu Mealie fehlgeschlagen: {err}") from err
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    async def whoami(self) -> dict[str, Any]:
        """Return the user the token belongs to."""
        return await self.request("GET", "/api/users/self")

    def recipe_url(self, slug: str, group_slug: str | None) -> str:
        """Browser URL of a recipe."""
        if group_slug:
            return f"{self.base_url}/g/{group_slug}/r/{slug}"
        return f"{self.base_url}/recipe/{slug}"

    async def get_recipe(self, slug: str) -> dict[str, Any]:
        """Fetch a recipe."""
        return await self.request("GET", f"/api/recipes/{slug}")

    async def create_from_json(self, data: str, include_tags: bool = True) -> str:
        """Create a recipe from schema.org JSON or an HTML page. Returns the slug."""
        return await self.request(
            "POST",
            "/api/recipes/create/html-or-json",
            json_body={"data": data, "includeTags": include_tags},
        )

    async def create_recipe(self, data: dict[str, Any]) -> str:
        """Create a recipe from structured fields. Returns the slug."""
        schema = build_schema_recipe(data)
        try:
            slug = await self.create_from_json(json.dumps(schema, ensure_ascii=False))
        except MealieApiError as err:
            if err.status not in (404, 405, 422):
                raise
            # Fallback for Mealie versions without the HTML/JSON importer.
            slug = await self.request("POST", "/api/recipes", json_body={"name": data["name"]})
            rest = {k: v for k, v in data.items() if k != "name"}
            if rest:
                slug = await self.update_recipe(slug, rest)
            return slug
        # Fields the schema.org importer does not reliably carry over.
        extra = {k: data[k] for k in ("notes", "categories") if data.get(k)}
        if extra:
            slug = await self.update_recipe(slug, extra)
        return slug

    async def update_recipe(self, slug: str, data: dict[str, Any]) -> str:
        """Patch the given fields. Returns the (possibly new) slug."""
        patch = build_patch(data)
        for key, kind, api_key in (
            ("tags", "tags", "tags"),
            ("categories", "categories", "recipeCategory"),
        ):
            if key in data:
                patch[api_key] = await self.resolve_organizers(kind, data[key] or [])
        if not patch:
            return slug
        result = await self.request("PATCH", f"/api/recipes/{slug}", json_body=patch)
        if isinstance(result, dict) and result.get("slug"):
            return result["slug"]
        return slug

    async def resolve_organizers(
        self, kind: str, names: list[str]
    ) -> list[dict[str, Any]]:
        """Map tag/category names to Mealie objects, creating missing ones."""
        if not names:
            return []
        existing = await self.request(
            "GET", f"/api/organizers/{kind}", params={"perPage": "-1"}
        )
        items = existing.get("items", []) if isinstance(existing, dict) else existing or []
        by_name = {
            str(item["name"]).casefold(): item for item in items if item.get("name")
        }
        result: list[dict[str, Any]] = []
        for name in names:
            item = by_name.get(name.casefold())
            if item is None:
                item = await self.request(
                    "POST", f"/api/organizers/{kind}", json_body={"name": name}
                )
                by_name[name.casefold()] = item
            result.append(
                {"id": item["id"], "name": item["name"], "slug": item["slug"]}
            )
        return result

    async def delete_recipe(self, slug: str) -> None:
        """Delete a recipe."""
        await self.request("DELETE", f"/api/recipes/{slug}")

    async def set_image_from_url(self, slug: str, url: str) -> None:
        """Let Mealie download an image for the recipe."""
        await self.request(
            "POST",
            f"/api/recipes/{slug}/image",
            json_body={"url": url, "includeTags": False},
        )

    async def upload_image(self, slug: str, content: bytes, extension: str) -> None:
        """Upload an image file for the recipe."""
        form = aiohttp.FormData()
        form.add_field("image", content, filename=f"image.{extension}")
        form.add_field("extension", extension)
        await self.request("PUT", f"/api/recipes/{slug}/image", form=form)
