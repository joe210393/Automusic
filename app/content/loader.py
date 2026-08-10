"""讀取 destinations 內容包（可寫目錄覆寫 + 內建 seed）。"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from app.content import store

_KEYWORD_SPLIT = re.compile(r"[,，、\s]+")


@lru_cache(maxsize=1)
def list_destinations() -> List[dict]:
    return [
        d for d in store.list_all()
        if d.get("enabled", True)
    ]


@lru_cache(maxsize=32)
def load_destination(dest_id: str) -> Optional[Dict[str, Any]]:
    return store.get_destination(dest_id, include_disabled=True)


def resolve_engine_style(destination: dict, mood_id: str) -> str:
    """心情卡 id → theory style id。"""
    for m in destination.get("moodStyles", []):
        if m.get("id") == mood_id:
            return m.get("engineStyle") or m.get("style") or "pop"
    return "pop"


def split_keywords(text: str) -> List[str]:
    return [p.strip() for p in _KEYWORD_SPLIT.split(text or "") if p.strip()]


def story_to_keywords(story: dict, destination: Optional[dict] = None) -> List[str]:
    """作詞關鍵字：只用使用者提供的內容，不注入系統地名／路線／預設 chips。"""
    del destination  # 保留參數相容舊呼叫，刻意不使用
    keys: List[str] = []
    raw = story.get("keywords")
    if isinstance(raw, str):
        raw = split_keywords(raw)
    if isinstance(raw, list):
        for k in raw:
            s = str(k or "").strip()
            if s and s not in keys:
                keys.append(s[:24])
    # 使用者自填欄位（非系統選項）
    for field in ("place", "companions", "feeling", "memory"):
        v = (story.get(field) or "").strip()
        if v and v not in keys:
            keys.append(v[:24])
    return keys[:6]
