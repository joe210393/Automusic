"""
營運用量：進站、TOKEN 消耗帳本、儀表板統計。

TOKEN = 成品額度次數（不是 LLM API token）。
每首歌（journey）會累積 token_usage，帳號／全站也有 ledger。

實際運算見 app.ops.metering（LLM usage + 做歌 music_units）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from datetime import timedelta

from app.util.timeutil import now_iso, now_taipei, parse_to_taipei

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


def _day_key(value: Optional[str]) -> Optional[str]:
    dt = parse_to_taipei(value)
    return dt.strftime("%Y-%m-%d") if dt else None


def _empty_daily(days: int = 14) -> Dict[str, Dict[str, int]]:
    today = now_taipei().date()
    out: Dict[str, Dict[str, int]] = {}
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        out[d] = {"visits": 0, "games": 0, "tokens": 0, "finals": 0, "purchases": 0}
    return out


def ops_summary(days: int = 14) -> Dict[str, Any]:
    """營運儀表板數字 + 統計圖表資料。"""
    from app.journey import store as journey_store
    from app.ops import accounts as ops_accounts
    from app.ops import metering as ops_metering

    days = max(7, min(int(days or 14), 60))
    visits = _read_jsonl(VISITS_PATH)
    purchases = _read_jsonl(PURCHASES_PATH)
    ledger = _read_jsonl(LEDGER_PATH)

    accounts = ops_accounts.list_accounts()
    account_n = len(accounts)
    paid_n = sum(1 for a in accounts if a.get("paid"))

    daily = _empty_daily(days)
    day_set = set(daily.keys())

    # 旅程掃描
    games = 0
    with_sounds = 0
    with_final = 0
    tokens_on_songs = 0
    active_keys = set()
    dest_counts: Dict[str, int] = {}
    status_counts: Dict[str, int] = {}
    metas: List[dict] = []
    for path in journey_store.JOURNEYS_ROOT.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        metas.append(meta)
        games += 1
        dest = str(meta.get("destination") or "—")
        dest_counts[dest] = dest_counts.get(dest, 0) + 1
        st = str(meta.get("status") or "—")
        status_counts[st] = status_counts.get(st, 0) + 1
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

        created_day = _day_key(meta.get("created"))
        if created_day in day_set:
            daily[created_day]["games"] += 1
            if meta.get("final_file"):
                daily[created_day]["finals"] += 1

    # 進站 log → 日趨勢；無 log 時用旅程 created 近似 visits
    if visits:
        for row in visits:
            d = _day_key(row.get("at"))
            if d in day_set:
                daily[d]["visits"] += 1
    else:
        for d, bucket in daily.items():
            bucket["visits"] = bucket["games"]

    for row in ledger:
        d = _day_key(row.get("at"))
        if d in day_set:
            daily[d]["tokens"] += int(row.get("tokens") or 0)

    for row in purchases:
        d = _day_key(row.get("at"))
        if d in day_set:
            daily[d]["purchases"] += 1

    # 進站：有 visit log 用 log，否則用旅程數近似
    visit_n = len(visits) if visits else games
    users_n = len(active_keys)

    tokens_spent = sum(int(r.get("tokens") or 0) for r in ledger)
    if tokens_spent == 0:
        tokens_spent = tokens_on_songs

    token_by_kind: Dict[str, int] = {}
    for row in ledger:
        kind = str(row.get("kind") or "other")
        token_by_kind[kind] = token_by_kind.get(kind, 0) + int(row.get("tokens") or 0)
    if not token_by_kind and tokens_on_songs:
        token_by_kind["ai_finalize"] = tokens_on_songs

    compute = ops_metering.aggregate_from_journeys(metas)
    # 日趨勢：從 compute ledger 補 LLM／做歌
    for d, bucket in daily.items():
        bucket["llm_tokens"] = 0
        bucket["music_units"] = 0
    for row in ops_metering.read_compute_ledger():
        d = _day_key(row.get("at"))
        if d not in day_set:
            continue
        if row.get("channel") == "llm":
            daily[d]["llm_tokens"] += int(row.get("total_tokens") or 0)
        elif row.get("channel") == "music":
            daily[d]["music_units"] += int(row.get("units") or 0)

    recent_tokens = list(reversed(ledger[-30:]))
    recent_purchases = list(reversed(purchases[-20:]))
    dest_sorted = sorted(
        [{"id": k, "games": v} for k, v in dest_counts.items()],
        key=lambda x: -x["games"],
    )

    return {
        "kpis": {
            "visits": visit_n,
            "users": users_n,
            "accounts": account_n,
            "games": games,
            "purchases": len(purchases) if purchases else paid_n,
            "paid_accounts": paid_n,
            "with_sounds": with_sounds,
            "with_final": with_final,
            "tokens_spent": tokens_spent,
            "llm_tokens": compute["llm_tokens"],
            "music_units": compute["music_units"],
            "music_runs": compute["music_runs"],
        },
        "compute": compute,
        "destinations": dest_sorted,
        "recent_tokens": recent_tokens,
        "recent_compute": ops_metering.recent_compute(30),
        "recent_purchases": recent_purchases,
        "token_costs": dict(TOKEN_COSTS),
        "charts": {
            "days": days,
            "daily": [
                {"date": d, **vals}
                for d, vals in daily.items()
            ],
            "funnel": [
                {"key": "visits", "label": "進站", "value": visit_n},
                {"key": "sounds", "label": "有錄音", "value": with_sounds},
                {"key": "final", "label": "有成品", "value": with_final},
                {"key": "paid", "label": "付費帳號", "value": paid_n},
            ],
            "destinations": dest_sorted[:8],
            "token_by_kind": [
                {"kind": k, "tokens": v}
                for k, v in sorted(token_by_kind.items(), key=lambda x: -x[1])
            ],
            "status": [
                {"status": k, "count": v}
                for k, v in sorted(status_counts.items(), key=lambda x: -x[1])
            ],
        },
        "updated": now_iso(),
    }
