"""可寫入的目的地／旅程內容庫。

讀取順序：可寫目錄覆寫 → 內建 seed（app/content/destinations）。
寫入只落在可寫目錄（/voice/content 或專案 content/），不改動程式碼內的 seed。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

_BUNDLE = Path(__file__).parent / "destinations"
_persistent = Path("/voice")
_default_writable = (
    _persistent / "content" if _persistent.is_dir()
    else Path(__file__).resolve().parent.parent.parent / "content"
)
CONTENT_ROOT = Path(os.getenv("CONTENT_DIR", str(_default_writable)))
DEST_DIR = CONTENT_ROOT / "destinations"


def _ensure_seeded() -> None:
    """首次啟動：把內建 seed 複製到可寫目錄（已存在的檔案不覆蓋）。"""
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    if not _BUNDLE.exists():
        return
    for src in _BUNDLE.glob("*.json"):
        dst = DEST_DIR / src.name
        if not dst.exists():
            shutil.copy2(src, dst)


def clear_caches() -> None:
    from app.content import loader

    loader.list_destinations.cache_clear()
    loader.load_destination.cache_clear()


def _index_path() -> Path:
    return DEST_DIR / "index.json"


def _dest_path(dest_id: str) -> Path:
    if not SAFE_ID.match(dest_id):
        raise ValueError("目的地 id 只能用小寫英文、數字、_、-，且以字母開頭")
    return DEST_DIR / f"{dest_id}.json"


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def rebuild_index() -> List[dict]:
    _ensure_seeded()
    items = []
    for p in sorted(DEST_DIR.glob("*.json")):
        if p.name == "index.json":
            continue
        try:
            raw = _read_json(p) or {}
        except Exception:
            continue
        items.append({
            "id": raw.get("id", p.stem),
            "label": (raw.get("brand") or {}).get("place") or p.stem,
            "enabled": bool(raw.get("enabled", True)),
            "tagline": (raw.get("brand") or {}).get("headline") or "",
        })
    _write_json(_index_path(), {"destinations": items})
    clear_caches()
    return items


def list_all() -> List[dict]:
    _ensure_seeded()
    idx = _read_json(_index_path())
    if idx and isinstance(idx.get("destinations"), list):
        return idx["destinations"]
    return rebuild_index()


def get_destination(dest_id: str, *, include_disabled: bool = True) -> Optional[Dict[str, Any]]:
    _ensure_seeded()
    # 可寫目錄優先
    path = DEST_DIR / f"{dest_id}.json"
    if not path.exists():
        bundled = _BUNDLE / f"{dest_id}.json"
        if bundled.exists():
            data = _read_json(bundled)
        else:
            return None
    else:
        data = _read_json(path)
    if not data:
        return None
    data.setdefault("id", dest_id)
    data.setdefault("enabled", True)
    data.setdefault("routes", [])
    data.setdefault("moodStyles", [])
    data.setdefault("storyPrompts", {})
    data.setdefault("brand", {})
    if not include_disabled and not data.get("enabled", True):
        return None
    return data


def save_destination(dest_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not SAFE_ID.match(dest_id):
        raise ValueError("目的地 id 只能用小寫英文、數字、_、-，且以字母開頭")
    payload = dict(data)
    payload["id"] = dest_id
    payload.setdefault("enabled", True)
    payload.setdefault("brand", {})
    payload.setdefault("routes", [])
    payload.setdefault("moodStyles", [])
    payload.setdefault("storyPrompts", {})
    # 正規化 routes
    routes = []
    for r in payload.get("routes") or []:
        rid = (r.get("id") or "").strip()
        if not SAFE_ID.match(rid):
            raise ValueError(f"旅程 id 無效：{rid!r}")
        tasks = []
        for t in r.get("soundTasks") or []:
            tid = (t.get("id") or "").strip()
            if not SAFE_ID.match(tid):
                raise ValueError(f"聲音任務 id 無效：{tid!r}")
            tasks.append({"id": tid, "label": (t.get("label") or tid).strip()})
        if not tasks:
            raise ValueError(f"旅程「{r.get('label') or rid}」至少需要一個聲音任務")
        routes.append({
            "id": rid,
            "label": (r.get("label") or rid).strip(),
            "blurb": (r.get("blurb") or "").strip(),
            "soundTasks": tasks,
        })
    payload["routes"] = routes
    _write_json(_dest_path(dest_id), payload)
    rebuild_index()
    return payload


def create_destination(
    dest_id: str,
    *,
    place: str,
    headline: str = "",
    enabled: bool = True,
) -> Dict[str, Any]:
    _ensure_seeded()
    if (DEST_DIR / f"{dest_id}.json").exists():
        raise ValueError("目的地已存在")
    place = place.strip() or dest_id
    data = {
        "id": dest_id,
        "enabled": enabled,
        "brand": {
            "place": place,
            "headline": headline or f"把今天的{place}，變成一首屬於你的歌",
            "subhead": "收集旅途中遇見的聲音、風景與故事，最後讓這首歌用你的聲音唱出這趟旅行。",
            "cta": "開始我的音樂旅程",
            "coreLine": "不是來這裡做一首歌，而是把這趟旅行，帶回家變成一首歌。",
            "demoSongUrl": None,
        },
        "routes": [],
        "moodStyles": [
            {
                "id": "travel_light",
                "label": "輕快旅行",
                "emoji": "🎸",
                "engineStyle": "pop",
                "blurb": "步伐輕鬆、好哼好記",
            },
            {
                "id": "warm_memory",
                "label": "溫柔回憶",
                "emoji": "🎹",
                "engineStyle": "ballad",
                "blurb": "想把這一天好好留住",
            },
            {
                "id": "seaside_chill",
                "label": "海邊 Chill",
                "emoji": "🌊",
                "engineStyle": "ambient",
                "blurb": "慢一點，像風吹過港邊",
            },
        ],
        "storyPrompts": {
            "places": [],
            "companions": ["家人", "朋友", "另一半", "自己"],
            "feelings": ["快樂", "療癒", "冒險", "浪漫", "紀念"],
            "memoryPlaceholder": "例如：今天最想記住的一刻。",
        },
    }
    return save_destination(dest_id, data)


def upsert_route(dest_id: str, route: Dict[str, Any]) -> Dict[str, Any]:
    data = get_destination(dest_id)
    if not data:
        raise FileNotFoundError(dest_id)
    rid = (route.get("id") or "").strip()
    if not SAFE_ID.match(rid):
        raise ValueError("旅程 id 無效")
    routes = list(data.get("routes") or [])
    found = False
    for i, r in enumerate(routes):
        if r.get("id") == rid:
            routes[i] = route
            found = True
            break
    if not found:
        routes.append(route)
    data["routes"] = routes
    return save_destination(dest_id, data)


def delete_route(dest_id: str, route_id: str) -> Dict[str, Any]:
    data = get_destination(dest_id)
    if not data:
        raise FileNotFoundError(dest_id)
    before = len(data.get("routes") or [])
    data["routes"] = [r for r in (data.get("routes") or []) if r.get("id") != route_id]
    if len(data["routes"]) == before:
        raise KeyError(route_id)
    return save_destination(dest_id, data)


def slugify(text: str, fallback: str = "route") -> str:
    """中文／任意文字 → 可用 id（英數）。若無法轉出則用 fallback + 短碼。"""
    import hashlib
    import unicodedata

    s = unicodedata.normalize("NFKD", (text or "").strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if SAFE_ID.match(s or ""):
        return s[:32]
    h = hashlib.sha1((text or fallback).encode("utf-8")).hexdigest()[:6]
    base = re.sub(r"[^a-z0-9]", "", fallback)[:12] or "route"
    candidate = f"{base}-{h}"
    return candidate if SAFE_ID.match(candidate) else f"r-{h}"
