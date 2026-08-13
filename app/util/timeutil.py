"""台灣時間（UTC+8）統一寫入／顯示。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def now_iso() -> str:
    """存檔用：帶 +08:00 的 ISO 字串。"""
    return now_taipei().isoformat(timespec="seconds")


def month_key() -> str:
    """額度月份鍵（依台北日曆月）。"""
    return now_taipei().strftime("%Y-%m")


def parse_to_taipei(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TAIPEI)
        return dt.astimezone(TAIPEI)
    except Exception:
        return None


def format_display(value: str | None) -> str:
    """後端／除錯用顯示；前端另有 Intl 格式化。"""
    dt = parse_to_taipei(value)
    if not dt:
        return str(value) if value else "—"
    return dt.strftime("%Y-%m-%d %H:%M")
