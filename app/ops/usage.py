"""
營運用量：進站、TOKEN 消耗帳本、儀表板統計。

TOKEN = 成品額度次數（不是 LLM API token）。
每首歌（journey）會累積 token_usage，帳號／全站也有 ledger。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.util.timeutil import now_iso

_persistent = Path("/voice")
_default = (
    _persistent / "ops" if _persistent.is_dir()
    else Path(__file__).resolve().parent.parent.parent / "ops_data"
)
OPS_ROOT = Path(os.getenv("OPS_DIR", str(_default)))
OPS_ROOT.mkdir(parents=True, exist_ok=True)

LEDGER_PATH = OPS_ROOT / "token_ledger.jsonl"
VISITS_PATH = OPS_ROOT / "visits.jsonl"
PURCHASES_PATH = OPS_ROOT / "purchases.jsonl"

# 每種動作消耗的 TOKEN
TOKEN_COSTS = {
    "ai_finalize": 1,       # AI 唱歌成品
    "voice_finalize": 0,    # 聲紋版目前不另扣
    "full_upgrade": 1,      # 完整版升級（若啟用）
}


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path, limit: int = 5000) -> List[dict]:
    if not path.exists():
        return []
    rows: List[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def record_visit(
    *,
    journey_id: str,
    destination: str,
    account_id: Optional[str] = None,
) -> None:
    """進站：建立旅程時記錄。"""
    _append_jsonl(
        VISITS_PATH,
        {
            "at": now_iso(),
            "kind": "journey_start",
            "journey_id": journey_id,
            "destination": destination,
            "account_id": account_id,
        },
    )


def record_purchase(
    *,
    account_id: str,
    email: str = "",
    kind: str = "paid_plan",
) -> None:
    """購買人次：升級付費方案時記錄。"""
    _append_jsonl(
        PURCHASES_PATH,
        {
            "at": now_iso(),
            "kind": kind,
            "account_id": account_id,
            "email": email,
        },
    )


def record_token_spend(
    *,
    kind: str,
    journey_id: Optional[str],
    account_id: Optional[str],
    tokens: Optional[int] = None,
    meta: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    登記 TOKEN 使用，並寫回 journey.meta.token_usage。
    回傳本筆與累計。
    """
    cost = int(TOKEN_COSTS.get(kind, 0) if tokens is None else tokens)
    entry = {
        "at": now_iso(),
        "kind": kind,
        "tokens": cost,
        "journey_id": journey_id,
        "account_id": account_id or "anonymous",
    }
    _append_jsonl(LEDGER_PATH, entry)

    # 帳號側 ledger
    if account_id:
        from app.ops import accounts as ops_accounts

        acc = ops_accounts.get_account(account_id)
        if acc:
            ledger = acc.setdefault("token_ledger", [])
            ledger.append(entry)
            # 保留最近 200 筆
            if len(ledger) > 200:
                acc["token_ledger"] = ledger[-200:]
            month = __import__("app.util.timeutil", fromlist=["month_key"]).month_key()
            usage = acc.setdefault("usage", {})
            bucket = usage.setdefault(month, {"finalize": 0, "tokens": 0})
            bucket["tokens"] = int(bucket.get("tokens") or 0) + cost
            ops_accounts.save_account(acc)

    # 旅程側累計
    totals = {"total": 0, "by_kind": {}, "events": []}
    if journey_id and meta is not None:
        tu = meta.setdefault("token_usage", {"total": 0, "by_kind": {}, "events": []})
        tu["total"] = int(tu.get("total") or 0) + cost
        by_kind = tu.setdefault("by_kind", {})
        by_kind[kind] = int(by_kind.get(kind) or 0) + cost
        events = tu.setdefault("events", [])
        events.append({"at": entry["at"], "kind": kind, "tokens": cost})
        if len(events) > 50:
            tu["events"] = events[-50:]
        totals = {
            "total": tu["total"],
            "by_kind": dict(tu.get("by_kind") or {}),
            "events": list(tu.get("events") or []),
        }
        from app.journey import store as journey_store

        journey_store.save_meta(journey_id, meta)

    return {"entry": entry, "journey_tokens": totals}


def ops_summary() -> Dict[str, Any]:
    """營運儀表板數字。"""
    from app.journey import store as journey_store
    from app.ops import accounts as ops_accounts

    visits = _read_jsonl(VISITS_PATH)
    purchases = _read_jsonl(PURCHASES_PATH)
    ledger = _read_jsonl(LEDGER_PATH)

    accounts = ops_accounts.list_accounts()
    account_n = len(accounts)
    paid_n = sum(1 for a in accounts if a.get("paid"))

    # 旅程掃描
    games = 0
    with_sounds = 0
    with_final = 0
    tokens_on_songs = 0
    active_keys = set()
    dest_counts: Dict[str, int] = {}
    for path in journey_store.JOURNEYS_ROOT.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        games += 1
        dest = str(meta.get("destination") or "—")
        dest_counts[dest] = dest_counts.get(dest, 0) + 1
        if meta.get("sounds"):
            with_sounds += 1
        if meta.get("final_file"):
            with_final += 1
        tu = meta.get("token_usage") or {}
        tokens_on_songs += int(tu.get("total") or 0)
        aid = meta.get("account_id")
        if aid:
            active_keys.add(f"a:{aid}")
        else:
            active_keys.add(f"j:{meta.get('id') or path.parent.name}")

    # 進站：有 visit log 用 log，否則用旅程數近似
    visit_n = len(visits) if visits else games
    # 使用人數：有錄音或有成品的去重主體
    users_n = len(active_keys)

    tokens_spent = sum(int(r.get("tokens") or 0) for r in ledger)
    if tokens_spent == 0:
        tokens_spent = tokens_on_songs

    recent_tokens = list(reversed(ledger[-30:]))
    recent_purchases = list(reversed(purchases[-20:]))

    return {
        "kpis": {
            "visits": visit_n,              # 進站人數（旅程啟動）
            "users": users_n,               # 使用人數（有互動的去重）
            "accounts": account_n,          # 帳號數
            "games": games,                 # 遊戲數（旅程）
            "purchases": len(purchases) if purchases else paid_n,  # 購買人次
            "paid_accounts": paid_n,
            "with_sounds": with_sounds,
            "with_final": with_final,
            "tokens_spent": tokens_spent,
        },
        "destinations": sorted(
            [{"id": k, "games": v} for k, v in dest_counts.items()],
            key=lambda x: -x["games"],
        ),
        "recent_tokens": recent_tokens,
        "recent_purchases": recent_purchases,
        "token_costs": dict(TOKEN_COSTS),
        "updated": now_iso(),
    }
