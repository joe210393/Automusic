"""
每趟遊客旅程獨立目錄，避免聲紋／錄音互蓋。

目錄（優先 /voice/journeys，否則專案 journeys/）：
  {root}/{journey_id}/
    meta.json
    sounds/
    voiceprint/manifest.json + *.wav
    output/preview.mp3|wav, final.mp3|wav
"""
from __future__ import annotations

import json
import os
import secrets
import string
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.util.timeutil import now_iso as _now_iso

_persistent = Path("/voice")
_default_root = (
    _persistent / "journeys" if _persistent.is_dir()
    else Path(__file__).resolve().parent.parent.parent / "journeys"
)
JOURNEYS_ROOT = Path(os.getenv("JOURNEYS_DIR", str(_default_root)))
JOURNEYS_ROOT.mkdir(parents=True, exist_ok=True)

SAFE_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{8,64}$")
SLUG_ALPHABET = string.ascii_lowercase + string.digits

DEFAULT_COVERS = (
    "cover-01.png",
    "cover-02.png",
    "cover-03.png",
    "cover-04.png",
    "cover-05.png",
    "cover-06.png",
)
STOCK_COVER_PREFIX = "stock:"
STOCK_COVER_PUBLIC = "/trip/media/covers"


def new_journey_id() -> str:
    return secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]


def new_slug(length: int = 8) -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))


def journey_dir(journey_id: str) -> Path:
    if not SAFE_ID_RE.match(journey_id):
        raise ValueError("invalid journey id")
    return JOURNEYS_ROOT / journey_id


def ensure_layout(journey_id: str) -> Path:
    root = journey_dir(journey_id)
    (root / "sounds").mkdir(parents=True, exist_ok=True)
    (root / "voiceprint").mkdir(parents=True, exist_ok=True)
    (root / "output").mkdir(parents=True, exist_ok=True)
    return root


def meta_path(journey_id: str) -> Path:
    return journey_dir(journey_id) / "meta.json"


def load_meta(journey_id: str) -> Dict[str, Any]:
    path = meta_path(journey_id)
    if not path.exists():
        raise FileNotFoundError(journey_id)
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(journey_id: str, meta: Dict[str, Any]) -> None:
    ensure_layout(journey_id)
    meta["updated"] = _now_iso()
    meta_path(journey_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def create_journey(destination: str = "suao", account_id: Optional[str] = None) -> Dict[str, Any]:
    jid = new_journey_id()
    ensure_layout(jid)
    meta = {
        "id": jid,
        "slug": new_slug(),
        "created": _now_iso(),
        "updated": _now_iso(),
        "destination": destination,
        "route_id": None,
        "nickname": "",
        "companions": "",
        "feeling": "",
        "memory": "",
        "place": "",
        "mood_id": None,
        "engine_style": None,
        "sounds": [],
        "notes": None,
        "bpm": None,
        "key": None,
        "lyrics": None,
        "status": "created",
        "compose_steps": [],
        "preview_file": None,
        "final_file": None,
        "final_voice_file": None,
        "ai_singer_id": None,
        "voiceprint_consent": None,
        "title": "",
        "cover_file": f"{STOCK_COVER_PREFIX}{secrets.choice(DEFAULT_COVERS)}",
        "share_public": False,
        "account_id": account_id,
        "error": None,
        "token_usage": {"total": 0, "by_kind": {}, "events": []},
    }
    save_meta(jid, meta)
    try:
        from app.ops import usage as ops_usage

        ops_usage.record_visit(
            journey_id=jid,
            destination=destination,
            account_id=account_id,
        )
    except Exception as e:
        print(f"[journey] visit log skip: {e}")
    return meta


def find_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    if not slug or len(slug) > 32:
        return None
    for path in JOURNEYS_ROOT.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("slug") == slug and meta.get("share_public"):
            return meta
    return None


def sounds_dir(journey_id: str) -> Path:
    return ensure_layout(journey_id) / "sounds"


def voiceprint_dir(journey_id: str) -> Path:
    return ensure_layout(journey_id) / "voiceprint"


def output_dir(journey_id: str) -> Path:
    return ensure_layout(journey_id) / "output"


def load_voiceprint_manifest(journey_id: str) -> dict:
    path = voiceprint_dir(journey_id) / "manifest.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"lines": []}


def save_voiceprint_manifest(journey_id: str, manifest: dict) -> None:
    path = voiceprint_dir(journey_id) / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def cover_dir(journey_id: str) -> Path:
    d = ensure_layout(journey_id) / "cover"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stock_covers_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tourist" / "media" / "covers"


def pick_stock_cover(seed: str = "") -> str:
    """回傳 stock:cover-0N.png；有 seed 時為穩定選擇，否則隨機。"""
    if seed:
        idx = abs(hash(seed)) % len(DEFAULT_COVERS)
        return f"{STOCK_COVER_PREFIX}{DEFAULT_COVERS[idx]}"
    return f"{STOCK_COVER_PREFIX}{secrets.choice(DEFAULT_COVERS)}"


def is_stock_cover(cover_file: Optional[str]) -> bool:
    return not cover_file or str(cover_file).startswith(STOCK_COVER_PREFIX)


def resolve_cover_ref(meta: Dict[str, Any]) -> str:
    """正規化封面參照：stock:… 或上傳檔名。"""
    cover = meta.get("cover_file")
    if cover and not str(cover).startswith(STOCK_COVER_PREFIX):
        return str(cover)
    if cover and str(cover).startswith(STOCK_COVER_PREFIX):
        name = str(cover)[len(STOCK_COVER_PREFIX):]
        if name in DEFAULT_COVERS:
            return f"{STOCK_COVER_PREFIX}{name}"
    return pick_stock_cover(str(meta.get("id") or ""))


def cover_public_url(meta: Dict[str, Any]) -> Dict[str, Any]:
    jid = meta.get("id")
    ref = resolve_cover_ref(meta)
    if ref.startswith(STOCK_COVER_PREFIX):
        name = ref[len(STOCK_COVER_PREFIX):]
        stem = Path(name).stem
        return {
            "cover_url": f"{STOCK_COVER_PUBLIC}/{name}",
            "cover_url_webp": f"{STOCK_COVER_PUBLIC}/{stem}.webp",
            "cover_custom": False,
            "cover_file": ref,
        }
    return {
        "cover_url": f"/api/journey/{jid}/cover",
        "cover_url_webp": None,
        "cover_custom": True,
        "cover_file": ref,
    }


def display_title(meta: Dict[str, Any]) -> str:
    title = str(meta.get("title") or "").strip()
    if title:
        return title
    lyrics = meta.get("lyrics") or {}
    if isinstance(lyrics, dict):
        ly = str(lyrics.get("title") or "").strip()
        if ly:
            return ly
    return ""


def resume_screen(status: str) -> str:
    """依旅程狀態建議回到哪個遊客畫面。"""
    s = (status or "").strip()
    if s in ("done", "finalized"):
        return "result"
    if s in ("voicing", "finalizing"):
        return "result"
    if s == "style":
        return "voice"
    if s in ("preview", "composing", "error"):
        return "compose"
    if s == "story":
        return "mood"
    if s == "collecting":
        return "collect"
    if s == "route":
        return "collect"
    return "route"


def account_journey_card(meta: Dict[str, Any]) -> Dict[str, Any]:
    jid = meta.get("id")
    title = display_title(meta)
    cover = cover_public_url(meta)
    return {
        "id": jid,
        "slug": meta.get("slug"),
        "destination": meta.get("destination"),
        "route_id": meta.get("route_id"),
        "mood_id": meta.get("mood_id"),
        "nickname": meta.get("nickname"),
        "title": title or None,
        "created": meta.get("created"),
        "updated": meta.get("updated"),
        "status": meta.get("status"),
        "resume_screen": resume_screen(meta.get("status") or ""),
        "share_public": meta.get("share_public"),
        "has_final": bool(meta.get("final_file")),
        "has_voice_final": bool(meta.get("final_voice_file")),
        "has_preview": bool(meta.get("preview_file")),
        "ai_singer_id": meta.get("ai_singer_id"),
        "voiceprint_consent": bool((meta.get("voiceprint_consent") or {}).get("accepted")),
        "sound_count": len(meta.get("sounds") or []),
        "cover_url": cover["cover_url"],
        "cover_url_webp": cover.get("cover_url_webp"),
        "cover_custom": cover["cover_custom"],
        "is_complete": (meta.get("status") in ("done", "finalized")),
    }


def account_journey_detail(meta: Dict[str, Any]) -> Dict[str, Any]:
    jid = meta.get("id")
    card = account_journey_card(meta)
    lyrics = meta.get("lyrics") if isinstance(meta.get("lyrics"), dict) else None
    sounds = []
    for s in meta.get("sounds") or []:
        if not isinstance(s, dict) or not s.get("filename"):
            continue
        sounds.append({
            "slot": s.get("slot"),
            "label": s.get("label") or s.get("slot"),
            "filename": s.get("filename"),
            "url": f"/api/journey/{jid}/sounds/{s['filename']}",
        })
    card.update({
        "keywords": meta.get("keywords") or [],
        "memory": meta.get("memory") or "",
        "feeling": meta.get("feeling") or "",
        "lyrics": lyrics,
        "sounds": sounds,
        "preview_url": f"/api/journey/{jid}/audio/preview" if meta.get("preview_file") else None,
        "final_url": f"/api/journey/{jid}/audio/final" if meta.get("final_file") else None,
        "final_voice_url": f"/api/journey/{jid}/audio/final-voice" if meta.get("final_voice_file") else None,
        "share_path": f"/s/{meta.get('slug')}" if meta.get("share_public") and meta.get("slug") else None,
        "error": meta.get("error"),
        "versions": {
            "has_ai_final": bool(meta.get("final_file")),
            "has_voice_final": bool(meta.get("final_voice_file")),
        },
    })
    return card


def list_account_journeys(account_id: str) -> List[Dict[str, Any]]:
    out = []
    for path in JOURNEYS_ROOT.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("account_id") == account_id:
            out.append(account_journey_card(meta))
    out.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
    return out


def list_all_journeys(
    limit: int = 200,
    *,
    offset: int = 0,
    q: Optional[str] = None,
    status: Optional[str] = None,
    destination: Optional[str] = None,
    has_final: Optional[bool] = None,
) -> Dict[str, Any]:
    """後台用：列出旅程摘要，支援搜尋／篩選／分頁。"""
    out: List[Dict[str, Any]] = []
    needle = (q or "").strip().lower()
    status_f = (status or "").strip().lower()
    dest_f = (destination or "").strip().lower()
    for path in JOURNEYS_ROOT.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        jid = meta.get("id") or path.parent.name
        sounds = meta.get("sounds") or []
        keywords = meta.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords] if keywords.strip() else []
        st = str(meta.get("status") or "").lower()
        dest = str(meta.get("destination") or "").lower()
        if status_f and st != status_f:
            continue
        if dest_f and dest != dest_f:
            continue
        has_f = bool(meta.get("final_file"))
        if has_final is True and not has_f:
            continue
        if has_final is False and has_f:
            continue
        title = display_title(meta) or ""
        nick = meta.get("nickname") or ""
        if needle:
            blob = " ".join(
                [
                    jid,
                    title,
                    nick,
                    dest,
                    st,
                    " ".join(str(k) for k in keywords),
                    str(meta.get("account_id") or ""),
                    str(meta.get("route_id") or ""),
                ]
            ).lower()
            if needle not in blob:
                continue
        vp_path = journey_dir(jid) / "voiceprint" / "manifest.json"
        voice_lines = 0
        if vp_path.exists():
            try:
                vp = json.loads(vp_path.read_text(encoding="utf-8"))
                voice_lines = len((vp or {}).get("lines") or [])
            except Exception:
                voice_lines = 0
        cover_info = cover_public_url(meta)
        out.append({
            "id": jid,
            "slug": meta.get("slug"),
            "created": meta.get("created"),
            "updated": meta.get("updated"),
            "destination": meta.get("destination"),
            "route_id": meta.get("route_id"),
            "mood_id": meta.get("mood_id"),
            "nickname": nick,
            "status": meta.get("status"),
            "account_id": meta.get("account_id"),
            "share_public": bool(meta.get("share_public")),
            "keywords": keywords,
            "memory": meta.get("memory") or "",
            "feeling": meta.get("feeling") or "",
            "title": title or None,
            "cover_url": cover_info["cover_url"],
            "cover_custom": cover_info["cover_custom"],
            "sounds": [
                {
                    "slot": s.get("slot"),
                    "label": s.get("label") or s.get("slot"),
                    "filename": s.get("filename"),
                    "url": f"/api/admin/journeys/{jid}/sounds/{s.get('filename')}"
                    if s.get("filename") else None,
                }
                for s in sounds if isinstance(s, dict)
            ],
            "sound_count": len(sounds),
            "voice_lines": voice_lines,
            "preview_file": meta.get("preview_file"),
            "final_file": meta.get("final_file"),
            "preview_url": f"/api/journey/{jid}/audio/preview" if meta.get("preview_file") else None,
            "final_url": f"/api/journey/{jid}/audio/final" if meta.get("final_file") else None,
            "error": meta.get("error"),
            "token_usage": meta.get("token_usage") or {"total": 0, "by_kind": {}, "events": []},
            "tokens_used": (
                int((meta.get("token_usage") or {}).get("total") or 0)
                or (1 if meta.get("final_file") else 0)  # 舊資料：有成品則估 1 TOKEN
            ),
        })
    out.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
    total = len(out)
    page_size = max(1, min(int(limit or 20), 100))
    start = max(0, int(offset or 0))
    page = out[start : start + page_size]
    return {
        "journeys": page,
        "total": total,
        "offset": start,
        "limit": page_size,
    }
