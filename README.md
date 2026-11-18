# Mealie API für Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/atze09/home-assistant-mealie-api/actions/workflows/validate.yml/badge.svg)](https://github.com/atze09/home-assistant-mealie-api/actions/workflows/validate.yml)

Bindet die REST-API von [Mealie](https://mealie.io) als Aktionen in Home Assistant ein.
Ergänzt die offizielle Mealie-Integration um alles, was dort fehlt: Rezepte anlegen,
bearbeiten und löschen, Bilder setzen und beliebige API-Aufrufe. Alle Aktionen liefern
eine Antwort zurück und lassen sich deshalb auch von Sprachassistenten und über den
Home-Assistant-MCP-Server nutzen.

## Installation über HACS
1. HACS → ⋮ → **Benutzerdefinierte Repositories**
2. URL `https://github.com/atze09/home-assistant-mealie-api`, Typ **Integration**
3. „Mealie API“ installieren und Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → Integration hinzufügen → **Mealie API**
5. URL deiner Mealie-Instanz und einen API-Token eingeben (Mealie → Profil → API-Tokens)

### Manuell
Ordner `custom_components/mealie_api` nach `/config/custom_components/` kopieren und neu starten.

## Aktionen
| Aktion | Zweck |
|---|---|
| `mealie_api.create_recipe` | Rezept aus Name, Zutaten, Schritten, Zeiten, Tags anlegen |
| `mealie_api.update_recipe` | Felder eines Rezepts ändern (Zutaten/Schritte werden ersetzt) |
| `mealie_api.delete_recipe` | Rezept löschen |
| `mealie_api.create_from_json` | Rezept aus schema.org-JSON oder HTML anlegen |
| `mealie_api.set_recipe_image` | Bild per URL oder Datei setzen |
| `mealie_api.api_request` | Beliebiger Aufruf der Mealie-API (`/api/...`), gibt die Antwort zurück |

Zutaten und Schritte können als Liste oder als mehrzeiliger Text übergeben werden.
Zeiten als Minuten (`30`) oder als Text.

### Beispiel
```yaml
action: mealie_api.create_recipe
data:
  name: Shrimps in Paprika-Sahnesoße
  ingredients:
    - 500 g Shrimps
    - 3 Knoblauchzehen
  instructions:
    - Shrimps pulen und den Darm entfernen.
    - Olivenöl erhitzen, Knoblauch und Chili andünsten.
  total_time: 30
response_variable: rezept
```

```yaml
action: mealie_api.api_request
data:
  method: GET
  path: /api/households/shopping/lists
response_variable: listen
```

## Hinweis
Der API-Token hat die Rechte deines Mealie-Benutzers. `api_request` kann alles, was dieser Benutzer darf.

## Lizenz
MIT
