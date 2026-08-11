"""
簡易帳號與額度（檔案型，無外部 DB）。

- 匿名遊客：每趟 journey 可完成一次成品
- 登入帳號：每月成品次數（免費／加值方案）＋可加購本月額度
- paid / plan stub：金流 webhook 可之後接
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_persistent = Path("/voice")
_default = (
    _persistent / "accounts" if _persistent.is_dir()
    else Path(__file__).resolve().parent.parent.parent / "accounts"
)
ACCOUNTS_ROOT = Path(os.getenv("ACCOUNTS_DIR", str(_default)))
ACCOUNTS_ROOT.mkdir(parents=True, exist_ok=True)

# 免費／加值方案每月成品上限
FREE_FINALIZE_LIMIT = int(os.getenv("FREE_FINALIZE_LIMIT", "3"))
PAID_FINALIZE_LIMIT = int(os.getenv("PAID_FINALIZE_LIMIT", "30"))
# 本月加購單包次數（開發用 stub）
QUOTA_BONUS_PACK = int(os.getenv("QUOTA_BONUS_PACK", "5"))
QUOTA_BONUS_PACK_MAX = int(os.getenv("QUOTA_BONUS_PACK_MAX", "50"))

PLAN_FREE = "free"
PLAN_PLUS = "plus"

PLANS: Dict[str, Dict[str, Any]] = {
    PLAN_FREE: {
        "id": PLAN_FREE,
        "label": "免費方案",
        "finalize_limit": FREE_FINALIZE_LIMIT,
        "full_song": False,
        "blurb": f"每月 {FREE_FINALIZE_LIMIT} 次 AI 成品（試聽版）",
    },
    PLAN_PLUS: {
        "id": PLAN_PLUS,
        "label": "加值方案",
        "finalize_limit": PAID_FINALIZE_LIMIT,
        "full_song": True,
        "blurb": f"每月 {PAID_FINALIZE_LIMIT} 次成品，並解鎖完整歌曲",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


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
        "plan": PLAN_FREE,
        "created": _now(),
        "usage": {_month_key(): {"finalize": 0, "bonus": 0}},
    }
    _path(account_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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


def account_plan_id(acc: Optional[dict]) -> str:
    if not acc:
        return PLAN_FREE
    if acc.get("plan") in PLANS:
        return str(acc["plan"])
    # 舊帳號：paid=true 視為 plus
    return PLAN_PLUS if acc.get("paid") else PLAN_FREE


def plan_info(plan_id: Optional[str] = None, acc: Optional[dict] = None) -> Dict[str, Any]:
    pid = plan_id or account_plan_id(acc)
    return dict(PLANS.get(pid) or PLANS[PLAN_FREE])


def _month_bucket(acc: dict) -> dict:
    month = _month_key()
    usage = acc.setdefault("usage", {})
    bucket = usage.setdefault(month, {"finalize": 0, "bonus": 0})
    if "bonus" not in bucket:
        bucket["bonus"] = 0
    return bucket


def check_finalize_quota(account_id: Optional[str]) -> Dict[str, Any]:
    """回傳額度狀態。匿名一律允許（靠 journey 隔離）。"""
    if not account_id:
        return {
            "allowed": True,
            "remaining": None,
            "limit": None,
            "used": None,
            "bonus": None,
            "base_limit": None,
            "paid": False,
            "plan": PLAN_FREE,
            "anonymous": True,
        }
    acc = get_account(account_id)
    if not acc:
        return {
            "allowed": True,
            "remaining": None,
            "limit": None,
            "used": None,
            "bonus": None,
            "base_limit": None,
            "paid": False,
            "plan": PLAN_FREE,
            "anonymous": True,
        }
    plan = plan_info(acc=acc)
    bucket = _month_bucket(acc)
    base_limit = int(plan["finalize_limit"])
    bonus = int(bucket.get("bonus") or 0)
    limit = base_limit + bonus
    used = int(bucket.get("finalize", 0))
    remaining = max(0, limit - used)
    return {
        "allowed": remaining > 0,
        "remaining": remaining,
        "limit": limit,
        "used": used,
        "bonus": bonus,
        "base_limit": base_limit,
        "paid": account_plan_id(acc) == PLAN_PLUS or bool(acc.get("paid")),
        "plan": account_plan_id(acc),
        "plan_label": plan["label"],
        "anonymous": False,
        "month": _month_key(),
        "bonus_pack": QUOTA_BONUS_PACK,
    }


def consume_finalize(account_id: Optional[str]) -> None:
    if not account_id:
        return
    acc = get_account(account_id)
    if not acc:
        return
    bucket = _month_bucket(acc)
    bucket["finalize"] = int(bucket.get("finalize", 0)) + 1
    save_account(acc)


def can_full_song(account_id: Optional[str] = None, acc: Optional[dict] = None) -> bool:
    """加值方案：解鎖完整歌曲長度。"""
    if acc is None and account_id:
        acc = get_account(account_id)
    if not acc:
        return False
    return account_plan_id(acc) == PLAN_PLUS or bool(acc.get("paid"))


def set_paid(account_id: str, paid: bool = True) -> Optional[dict]:
    """向後相容：paid=true 等同升級 plus。"""
    return set_plan(account_id, PLAN_PLUS if paid else PLAN_FREE)


def set_plan(account_id: str, plan: str = PLAN_PLUS) -> Optional[dict]:
    acc = get_account(account_id)
    if not acc:
        return None
    if plan not in PLANS:
        raise ValueError("未知方案")
    acc["plan"] = plan
    acc["paid"] = plan == PLAN_PLUS
    save_account(acc)
    return acc


def add_quota_bonus(account_id: str, amount: Optional[int] = None) -> Optional[dict]:
    """本月加購成品次數（stub）。"""
    acc = get_account(account_id)
    if not acc:
        return None
    n = int(amount if amount is not None else QUOTA_BONUS_PACK)
    if n <= 0:
        raise ValueError("加購次數需為正整數")
    bucket = _month_bucket(acc)
    new_bonus = int(bucket.get("bonus") or 0) + n
    if new_bonus > QUOTA_BONUS_PACK_MAX:
        raise ValueError(f"本月加購上限為 {QUOTA_BONUS_PACK_MAX} 次")
    bucket["bonus"] = new_bonus
    save_account(acc)
    return acc


def list_upgrade_options(acc: Optional[dict] = None) -> list:
    """前端展示用加值選項（金流之後可帶 price_id）。"""
    plan_id = account_plan_id(acc)
    options = []
    if plan_id != PLAN_PLUS:
        plus = PLANS[PLAN_PLUS]
        options.append({
            "id": "upgrade_plus",
            "kind": "plan",
            "plan": PLAN_PLUS,
            "label": plus["label"],
            "blurb": plus["blurb"],
            "stub": True,
        })
    options.append({
        "id": "bonus_pack",
        "kind": "bonus",
        "amount": QUOTA_BONUS_PACK,
        "label": f"加購本月成品 +{QUOTA_BONUS_PACK} 次",
        "blurb": f"立刻增加本月可用成品次數（單月加購上限 {QUOTA_BONUS_PACK_MAX}）",
        "stub": True,
    })
    return options


def public_account_view(acc: dict) -> dict:
    plan = plan_info(acc=acc)
    return {
        "id": acc["id"],
        "email": acc["email"],
        "display_name": acc.get("display_name"),
        "paid": account_plan_id(acc) == PLAN_PLUS or bool(acc.get("paid")),
        "plan": plan["id"],
        "plan_label": plan["label"],
        "can_full_song": can_full_song(acc=acc),
        "quota": check_finalize_quota(acc["id"]),
        "upgrades": list_upgrade_options(acc),
        "plans": [
            {
                "id": p["id"],
                "label": p["label"],
                "finalize_limit": p["finalize_limit"],
                "full_song": p["full_song"],
                "blurb": p["blurb"],
            }
            for p in PLANS.values()
        ],
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
            "paid": account_plan_id(data) == PLAN_PLUS or bool(data.get("paid")),
            "plan": account_plan_id(data),
            "created": data.get("created"),
            "quota": check_finalize_quota(data["id"]),
        })
    out.sort(key=lambda x: x.get("created") or "", reverse=True)
    return out


def account_map() -> Dict[str, Dict[str, Any]]:
    return {a["id"]: a for a in list_accounts()}
