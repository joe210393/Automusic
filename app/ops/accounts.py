"""
簡易帳號與額度（檔案型，無外部 DB）。

- 匿名遊客：每趟 journey 可完成一次成品
- 登入帳號（email + 魔術連結 token）：可看作品庫、較高額度
- 付費旗標 paid：解鎖下載／分享強化（金流 webhook 可之後接）
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from app.util.timeutil import month_key as _month_key
from app.util.timeutil import now_iso as _now

_persistent = Path("/voice")
_default = (
    _persistent / "accounts" if _persistent.is_dir()
    else Path(__file__).resolve().parent.parent.parent / "accounts"
)
ACCOUNTS_ROOT = Path(os.getenv("ACCOUNTS_DIR", str(_default)))
ACCOUNTS_ROOT.mkdir(parents=True, exist_ok=True)

# 免費／付費額度（每月）
FREE_FINALIZE_LIMIT = int(os.getenv("FREE_FINALIZE_LIMIT", "3"))
PAID_FINALIZE_LIMIT = int(os.getenv("PAID_FINALIZE_LIMIT", "30"))


def _path(account_id: str) -> Path:
    return ACCOUNTS_ROOT / f"{account_id}.json"


def create_account(email: str, display_name: str = "") -> Dict[str, Any]:
    email = email.strip().lower()
    if "@" not in email or len(email) > 120:
        raise ValueError("email 無效")
    account_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    data = {
        "id": account_id,
        "email": email,
        "display_name": display_name or email.split("@")[0],
        "token": token,
        "paid": False,
        "created": _now(),
        "usage": {_month_key(): {"finalize": 0}},
    }
    _path(account_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # email → id 索引
    idx = _load_index()
    idx[email] = account_id
    _save_index(idx)
    return data


def _load_index() -> dict:
    p = ACCOUNTS_ROOT / "index.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index(idx: dict) -> None:
    (ACCOUNTS_ROOT / "index.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_account(account_id: str) -> Optional[Dict[str, Any]]:
    p = _path(account_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def get_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or len(token) < 10:
        return None
    for p in ACCOUNTS_ROOT.glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("token") == token:
            return data
    return None


def get_by_email(email: str) -> Optional[Dict[str, Any]]:
    email = email.strip().lower()
    idx = _load_index()
    if email not in idx:
        return None
    return get_account(idx[email])


def get_or_create_by_email(email: str) -> Dict[str, Any]:
    email = email.strip().lower()
    existing = get_by_email(email)
    if existing:
        return existing
    return create_account(email)


def register_account(email: str, display_name: str = "") -> Dict[str, Any]:
    """註冊：email 已存在則失敗。"""
    email = email.strip().lower()
    if get_by_email(email):
        raise ValueError("這個 email 已經註冊過了，請直接登入")
    return create_account(email, display_name=display_name)


def login_by_email(email: str) -> Dict[str, Any]:
    """登入：必須已註冊。"""
    email = email.strip().lower()
    acc = get_by_email(email)
    if not acc:
        raise ValueError("找不到這個帳號，請先註冊")
    return acc


def save_account(data: Dict[str, Any]) -> None:
    _path(data["id"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def check_finalize_quota(account_id: Optional[str]) -> Dict[str, Any]:
    """回傳 {allowed, remaining, limit, paid}。匿名一律允許（靠 journey 隔離）。"""
    if not account_id:
        return {"allowed": True, "remaining": None, "limit": None, "paid": False, "anonymous": True}
    acc = get_account(account_id)
    if not acc:
        return {"allowed": True, "remaining": None, "limit": None, "paid": False, "anonymous": True}
    month = _month_key()
    usage = acc.setdefault("usage", {})
    bucket = usage.setdefault(month, {"finalize": 0})
    limit = PAID_FINALIZE_LIMIT if acc.get("paid") else FREE_FINALIZE_LIMIT
    used = int(bucket.get("finalize", 0))
    remaining = max(0, limit - used)
    return {
        "allowed": remaining > 0,
        "remaining": remaining,
        "limit": limit,
        "paid": bool(acc.get("paid")),
        "anonymous": False,
    }


def consume_finalize(
    account_id: Optional[str],
    *,
    journey_id: Optional[str] = None,
    meta: Optional[dict] = None,
    kind: str = "ai_finalize",
) -> Dict[str, Any]:
    """扣額度並登記 TOKEN（寫入帳號 ledger + 旅程 token_usage）。"""
    from app.ops import usage as ops_usage

    # 月額度計數仍保留（匿名不計）
    if account_id:
        acc = get_account(account_id)
        if acc:
            month = _month_key()
            usage = acc.setdefault("usage", {})
            bucket = usage.setdefault(month, {"finalize": 0, "tokens": 0})
            if kind == "ai_finalize":
                bucket["finalize"] = int(bucket.get("finalize", 0)) + 1
            save_account(acc)

    return ops_usage.record_token_spend(
        kind=kind,
        journey_id=journey_id,
        account_id=account_id,
        meta=meta,
    )


def set_paid(account_id: str, paid: bool = True) -> Optional[dict]:
    acc = get_account(account_id)
    if not acc:
        return None
    was_paid = bool(acc.get("paid"))
    acc["paid"] = paid
    save_account(acc)
    if paid and not was_paid:
        from app.ops import usage as ops_usage

        ops_usage.record_purchase(
            account_id=account_id,
            email=str(acc.get("email") or ""),
            kind="paid_plan",
        )
    return acc


def public_account_view(acc: dict) -> dict:
    month = _month_key()
    bucket = (acc.get("usage") or {}).get(month) or {}
    return {
        "id": acc["id"],
        "email": acc["email"],
        "display_name": acc.get("display_name"),
        "paid": bool(acc.get("paid")),
        "quota": check_finalize_quota(acc["id"]),
        "tokens_used_month": int(bucket.get("tokens") or bucket.get("finalize") or 0),
    }


def list_accounts() -> list:
    out = []
    for p in ACCOUNTS_ROOT.glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        out.append({
            "id": data["id"],
            "email": data.get("email"),
            "display_name": data.get("display_name"),
            "paid": bool(data.get("paid")),
            "created": data.get("created"),
            "quota": check_finalize_quota(data["id"]),
        })
    out.sort(key=lambda x: x.get("created") or "", reverse=True)
    return out


def account_map() -> Dict[str, Dict[str, Any]]:
    return {a["id"]: a for a in list_accounts()}
