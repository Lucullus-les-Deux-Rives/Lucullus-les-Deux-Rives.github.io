
import json
import os
import time
import hashlib
import mimetypes
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# -----------------------------------
# CONFIGURATION DE LDR.WORLD
# -----------------------------------

TOKEN = os.environ.get("NOTION_TOKEN")

ROOT_PAGE = "41b9b1f3b04a8103b749f86b0c758a2a"
API = "https://api.notion.com/v1"

OUTPUT = Path("data/notion.json")
AGENDA_OUTPUT = Path("data/agenda.json")
CALENDAR_DATABASE = "4229b1f3b04a8139ad6cd34545381e02"
ASSETS = Path("assets/notion")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json"
}

if not TOKEN:
    raise SystemExit("NOTION_TOKEN est absent des secrets GitHub.")

ASSETS.mkdir(parents=True, exist_ok=True)

visited = set()
calendar_entries = {}
statistics = {
    "pages": 0,
    "blocks": 0,
    "images": 0,
    "databases": 0,
    "errors": []
}


# -----------------------------------
# COMMUNICATION AVEC NOTION
# -----------------------------------

def api_request(path, method="GET", payload=None, params=None):
    url = API + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    for attempt in range(5):
        request = urllib.request.Request(
            url,
            data=body,
            headers=HEADERS,
            method=method
        )

        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                result = json.load(response)
                time.sleep(0.35)
                return result

        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504):
                if attempt < 4:
                    retry = error.headers.get("Retry-After", "3")
                    try:
                        delay = float(retry)
                    except ValueError:
                        delay = 3
                    time.sleep(max(1, delay))
                    continue

            raise RuntimeError(
                f"Notion API {error.code}: {path}"
            ) from error

    raise RuntimeError(f"Ã‰chec API Notion : {path}")


def paginate(path, method="GET", payload=None):
    results = []
    cursor = None

    while True:
        if method == "POST":
            data = dict(payload or {})
            data["page_size"] = 100
            if cursor:
                data["start_cursor"] = cursor

            result = api_request(path, "POST", data)

        else:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor

            result = api_request(path, params=params)

        results.extend(result.get("results", []))

        if not result.get("has_more"):
            break

        cursor = result.get("next_cursor")
        if not cursor:
            break

    return results


# -----------------------------------
# TEXTES ET MISE EN FORME
# -----------------------------------

def plain_text(items):
    return "".join(
        item.get("plain_text", "")
        for item in items or []
    )


def rich_text(items):
    result = []

    for item in items or []:
        annotations = item.get("annotations") or {}
        href = item.get("href")
        mention = item.get("mention") or {}
        if not href and mention.get("type") in ("page", "database"):
            target = mention.get(mention["type"], {}).get("id")
            if target:
                href = "https://app.notion.com/p/" + target.replace("-", "")

        result.append({
            "text": item.get("plain_text", ""),
            "href": href,
            "type": item.get("type"),
            "bold": annotations.get("bold", False),
            "italic": annotations.get("italic", False),
            "underline": annotations.get("underline", False),
            "strikethrough": annotations.get("strikethrough", False),
            "code": annotations.get("code", False),
            "color": annotations.get("color", "default")
        })

    return result


def page_title(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return plain_text(prop.get("title", []))

    return "Page sans titre"


# -----------------------------------
# PHOTOGRAPHIES ET FICHIERS
# -----------------------------------

def download_media(media, block_id):
    kind = media.get("type")
    source = (media.get(kind) or {}).get("url")

    if not source or not source.startswith("https://"):
        return None

    parsed = urllib.parse.urlparse(source)
    extension = Path(parsed.path).suffix.lower()

    valid = {
        ".jpg", ".jpeg", ".png", ".gif",
        ".webp", ".svg", ".avif",
        ".pdf", ".mp4", ".webm", ".mp3"
    }

    if extension not in valid:
        extension = ".bin"

    identifier = hashlib.sha256(
        block_id.encode("utf-8")
    ).hexdigest()[:20]

    filename = identifier + extension
    destination = ASSETS / filename

    try:
        request = urllib.request.Request(
            source,
            headers={"User-Agent": "LDR-world-sync/1.0"}
        )

        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip()

            if extension == ".bin":
                guessed = mimetypes.guess_extension(content_type)
                if guessed in valid:
                    destination = ASSETS / (identifier + guessed)

            # Limite de sÃ©curitÃ© : 30 Mo par fichier.
            max_size = 30 * 1024 * 1024
            content = response.read(max_size + 1)

            if len(content) > max_size:
                raise ValueError("Fichier supÃ©rieur Ã  30 Mo")

        destination.write_bytes(content)
        statistics["images"] += 1

        return "/" + destination.as_posix()

    except Exception as error:
        statistics["errors"].append(
            f"MÃ©dia {block_id}: {error}"
        )
        print(f"âš  MÃ©dia non tÃ©lÃ©chargÃ© : {block_id}")
        return None


# -----------------------------------
# LECTURE DES BLOCS NOTION
# -----------------------------------

MEDIA_TYPES = {
    "image", "video", "audio", "pdf", "file"
}


def read_blocks(block_id, depth=0):
    if depth > 30:
        raise RuntimeError("Profondeur de blocs dÃ©passÃ©e")

    raw_blocks = paginate(
        f"/blocks/{block_id}/children"
    )

    result = []

    for block in raw_blocks:
        statistics["blocks"] += 1

        block_type = block.get("type", "unsupported")
        properties = block.get(block_type) or {}

        item = {
            "id": block["id"],
            "type": block_type,
            "text": plain_text(properties.get("rich_text", [])),
            "rich_text": rich_text(
                properties.get("rich_text", [])
            ),
            "children": []
        }

        if block_type == "child_page":
            item["title"] = properties.get("title", "")
            item["page_id"] = block["id"]

        if block_type == "child_database":
            item["title"] = properties.get("title", "")
            item["database_id"] = block["id"]

        if block_type in MEDIA_TYPES:
            item["caption"] = plain_text(
                properties.get("caption", [])
            )
            item["src"] = download_media(
                properties, block["id"]
            )

        if block_type == "bookmark":
            item["url"] = properties.get("url")

        if block_type == "embed":
            item["url"] = properties.get("url")

        if block_type == "link_to_page":
            item["link_to_page"] = properties

        if block_type == "table":
            item["table_width"] = properties.get(
                "table_width", 0
            )
            item["has_column_header"] = properties.get(
                "has_column_header", False
            )

        if block_type == "table_row":
            item["cells"] = [
                rich_text(cell)
                for cell in properties.get("cells", [])
            ]

        if block_type == "to_do":
            item["checked"] = properties.get(
                "checked", False
            )

        if block_type == "code":
            item["language"] = properties.get(
                "language", "plain text"
            )

        if (
            block.get("has_children")
            and block_type not in ("child_page", "child_database")
        ):
            item["children"] = read_blocks(
                block["id"], depth + 1
            )

        result.append(item)

    return result


# -----------------------------------
# PARCOURS DES PAGES
# -----------------------------------

def find_subpages(blocks):
    pages = []
    databases = []

    def explore(items):
        for item in items:
            if item["type"] == "child_page":
                pages.append(item["page_id"])

            elif item["type"] == "child_database":
                databases.append(item["database_id"])

            explore(item.get("children", []))

    explore(blocks)
    return pages, databases


def calendar_entry(page):
    properties = page.get("properties", {})
    date = properties.get("Date", {}).get("date") or {}
    start = date.get("start")
    if not start:
        return None

    category = properties.get("CatÃ©gorie", {}).get("select") or {}
    recurrence = properties.get("RÃ©currence", {}).get("select") or {}
    entry = {
        "id": page["id"].replace("-", ""),
        "title": page_title(page),
        "date": start[:10],
        "place": plain_text(properties.get("Lieu", {}).get("rich_text", [])),
        "kind": category.get("name", ""),
        "recurrence": recurrence.get("name", "")
    }
    if date.get("end"):
        entry["end"] = date["end"][:10]
    return entry


def read_database(database_id):
    print(f"ðŸ“Š Base de donnÃ©es : {database_id}")

    database = api_request(
        f"/databases/{database_id}"
    )

    statistics["databases"] += 1
    pages = []
    events = {}
    is_calendar = database_id.replace("-", "") == CALENDAR_DATABASE

    for source in database.get("data_sources", []):
        source_id = source["id"]

        entries = paginate(
            f"/data_sources/{source_id}/query",
            method="POST"
        )

        for entry in entries:
            if entry.get("object") != "page":
                continue

            if is_calendar and not entry.get("archived") and not entry.get("in_trash"):
                event = calendar_entry(entry)
                if event:
                    events[event["id"]] = event

            page = read_page(entry["id"])
            if page:
                pages.append(page)

    if is_calendar:
        calendar_entries.update(events)
    return pages


def read_page(page_id):
    if page_id in visited:
        return None

    visited.add(page_id)

    page = api_request(
        f"/pages/{page_id}"
    )

    title = page_title(page)

    print(f"ðŸ“– {title}")
    statistics["pages"] += 1

    blocks = read_blocks(page_id)
    subpage_ids, database_ids = find_subpages(blocks)

    subpages = []

    for child_id in subpage_ids:
        child = read_page(child_id)
        if child:
            subpages.append(child)

    for database_id in database_ids:
        try:
            subpages.extend(
                read_database(database_id)
            )
        except Exception as error:
            statistics["errors"].append(
                f"Base {database_id}: {error}"
            )
            print(f"âš  Base inaccessible : {database_id}")

    return {
        "id": page_id,
        "title": title,
        "url": page.get("url"),
        "last_edited_time": page.get("last_edited_time"),
        "blocks": blocks,
        "subpages": subpages
    }


# -----------------------------------
# EXPORT POUR GITHUB PAGES
# -----------------------------------

def main():
    print("ðŸ›ï¸ Synchronisation complÃ¨te de LDR.world")

    root = read_page(ROOT_PAGE)

    data = {
        "site": "LDR.world",
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "root": root,
        "statistics": statistics
    }

    OUTPUT.parent.mkdir(
        parents=True, exist_ok=True
    )

    OUTPUT.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    if calendar_entries:
        agenda = sorted(calendar_entries.values(), key=lambda event: (event["date"], event["title"]))
        AGENDA_OUTPUT.write_text(json.dumps(agenda, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("----------------------------")
    print("Pages :", statistics["pages"])
    print("Blocs :", statistics["blocks"])
    print("MÃ©dias :", statistics["images"])
    print("Bases :", statistics["databases"])
    print("Erreurs :", len(statistics["errors"]))
    if calendar_entries:
        agenda = sorted(calendar_entries.values(), key=lambda event: (event["date"], event["title"]))
        AGENDA_OUTPUT.write_text(json.dumps(agenda, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("----------------------------")

    for error in statistics["errors"]:
        print("âš ", error)

    print("âœ… Export Notion terminÃ©")


if __name__ == "__main__":
    main()
