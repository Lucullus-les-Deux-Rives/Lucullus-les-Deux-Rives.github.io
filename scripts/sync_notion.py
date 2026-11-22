
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOKEN = os.environ.get("NOTION_TOKEN")
ROOT_PAGE = "41b9b1f3b04a8103b749f86b0c758a2a"
API_URL = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

if not TOKEN:
    raise SystemExit("Erreur : NOTION_TOKEN est absent.")


def notion_get(path, params=None):
    url = API_URL + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 4:
                delay = int(error.headers.get("Retry-After", "3"))
                time.sleep(max(delay, 1))
                continue
            raise

    raise RuntimeError("Impossible de contacter Notion.")


def get_children(block_id):
    blocks = []
    cursor = None

    while True:
        params = {"page_size": 100}

        if cursor:
            params["start_cursor"] = cursor

        data = notion_get(
            f"/blocks/{block_id}/children",
            params
        )

        blocks.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        cursor = data.get("next_cursor")

        if not cursor:
            break

    return blocks


def plain_text(rich_text):
    return "".join(
        element.get("plain_text", "")
        for element in rich_text
    )


def read_blocks(block_id, depth=0):
    if depth > 20:
        raise RuntimeError("Profondeur maximale atteinte.")

    result = []

    for block in get_children(block_id):
        block_type = block["type"]
        properties = block.get(block_type, {})

        item = {
            "id": block["id"],
            "type": block_type,
            "text": plain_text(properties.get("rich_text", [])),
        }

        if block_type == "child_page":
            item["title"] = properties.get("title", "")
            item["page_id"] = block["id"]

        if block_type == "image":
            item["caption"] = plain_text(
                properties.get("caption", [])
            )
            # Les URLs de fichiers Notion expirent.
            # Leur téléchargement sera ajouté ensuite.

        if block.get("has_children") and block_type != "child_page":
            item["children"] = read_blocks(
                block["id"], depth + 1
            )

        result.append(item)

    return result


visited = set()


def read_page(page_id):
    if page_id in visited:
        return None

    visited.add(page_id)
    page = notion_get(f"/pages/{page_id}")

    title = "Sans titre"

    for property_value in page.get("properties", {}).values():
        if property_value.get("type") == "title":
            title = plain_text(
                property_value.get("title", [])
            )
            break

    print(f"Lecture : {title}")

    blocks = read_blocks(page_id)
    subpages = []

    for block in blocks:
        if block["type"] == "child_page":
            child = read_page(block["page_id"])
            if child:
                subpages.append(child)

    return {
        "id": page_id,
        "title": title,
        "blocks": blocks,
        "subpages": subpages,
    }


print("Synchronisation de LDR.world depuis Notion")

data = {
    "site": "LDR.world",
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "root": read_page(ROOT_PAGE),
}

output = Path("data/notion.json")
output.parent.mkdir(parents=True, exist_ok=True)

output.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(f"Synchronisation terminée : {len(visited)} pages.")
print(f"Données enregistrées dans {output}")
