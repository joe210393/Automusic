"""
實際運算用量（與產品 TOKEN／成品額度分開）。

- LLM：OpenAI 相容 API 回傳的 usage（prompt／completion／total）
- 做歌：本機 ACE-Step 無雲端 credit，以秒數 × 步數估算 music_units，並記耗時
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
COMPUTE_LEDGER_PATH = OPS_ROOT / "compute_ledger.jsonl"


def extract_openai_usage(payload: Any) -> Optional[Dict[str, int]]:
    """從 chat/completions JSON 取出 token 用量。"""
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    if total <= 0 and prompt <= 0 and completion <= 0:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total or (prompt + completion),
    }


def estimate_music_units(*, duration_sec: float, inference_steps: int) -> int:
    """
    本機 ACE 相對計算量：約 45 秒 × 8 steps ≈ 8 units。
    沒有雲端帳單時用此當可比的「做歌單位」。
    """
    dur = max(1.0, float(duration_sec or 0))
    steps = max(1, int(inference_steps or 1))
    return max(1, int(round(dur * steps / 45.0)))


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


def _ensure_compute(meta: dict) -> dict:
    cu = meta.setdefault(
        "compute_usage",
        {
            "llm": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
            },
            "music": {
                "runs": 0,
                "duration_sec": 0.0,
                "elapsed_ms": 0,
                "units": 0,
            },
            "events": [],
        },
    )
    cu.setdefault(
        "llm",
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0},
    )
    cu.setdefault(
        "music",
        {"runs": 0, "duration_sec": 0.0, "elapsed_ms": 0, "units": 0},
    )
    cu.setdefault("events", [])
    return cu


def record_llm_usage(
    meta: dict,
    *,
    journey_id: Optional[str],
    kind: str,
    usage: Dict[str, int],
    model: Optional[str] = None,
    save: bool = True,
) -> dict:
    """登記一次 LLM 呼叫用量到 journey.meta.compute_usage。"""
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    cu = _ensure_compute(meta)
    llm = cu["llm"]
    llm["prompt_tokens"] = int(llm.get("prompt_tokens") or 0) + prompt
    llm["completion_tokens"] = int(llm.get("completion_tokens") or 0) + completion
    llm["total_tokens"] = int(llm.get("total_tokens") or 0) + total
    llm["calls"] = int(llm.get("calls") or 0) + 1

    entry = {
        "at": now_iso(),
        "kind": kind,
        "channel": "llm",
        "journey_id": journey_id or meta.get("id"),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "model": model,
    }
    events = cu.setdefault("events", [])
    events.append(entry)
    if len(events) > 80:
        cu["events"] = events[-80:]
    _append_jsonl(COMPUTE_LEDGER_PATH, entry)

    if save and journey_id:
        from app.journey import store as journey_store

        journey_store.save_meta(journey_id, meta)
    return entry


def record_music_usage(
    meta: dict,
    *,
    journey_id: Optional[str],
    kind: str = "ai_finalize",
    duration_sec: float,
    inference_steps: int,
    elapsed_ms: int,
    engine: str = "acestep",
    model: Optional[str] = None,
    via: Optional[str] = None,
    save: bool = True,
) -> dict:
    """登記一次做歌（ACE 等）到 journey.meta.compute_usage。"""
    units = estimate_music_units(
        duration_sec=duration_sec, inference_steps=inference_steps
    )
    cu = _ensure_compute(meta)
    music = cu["music"]
    music["runs"] = int(music.get("runs") or 0) + 1
    music["duration_sec"] = float(music.get("duration_sec") or 0) + float(duration_sec or 0)
    music["elapsed_ms"] = int(music.get("elapsed_ms") or 0) + int(elapsed_ms or 0)
    music["units"] = int(music.get("units") or 0) + units

    entry = {
        "at": now_iso(),
        "kind": kind,
        "channel": "music",
        "journey_id": journey_id or meta.get("id"),
        "duration_sec": float(duration_sec or 0),
        "inference_steps": int(inference_steps or 0),
        "elapsed_ms": int(elapsed_ms or 0),
        "units": units,
        "engine": engine,
        "model": model,
        "via": via,
    }
    events = cu.setdefault("events", [])
    events.append(entry)
    if len(events) > 80:
        cu["events"] = events[-80:]
    _append_jsonl(COMPUTE_LEDGER_PATH, entry)

    if save and journey_id:
        from app.journey import store as journey_store

        journey_store.save_meta(journey_id, meta)
    return entry


def aggregate_from_journeys(metas: List[dict]) -> Dict[str, Any]:
    llm_total = 0
    llm_prompt = 0
    llm_completion = 0
    llm_calls = 0
    music_units = 0
    music_runs = 0
    music_elapsed = 0
    music_duration = 0.0
    for meta in metas:
        cu = meta.get("compute_usage") or {}
        llm = cu.get("llm") or {}
        music = cu.get("music") or {}
        llm_total += int(llm.get("total_tokens") or 0)
        llm_prompt += int(llm.get("prompt_tokens") or 0)
        llm_completion += int(llm.get("completion_tokens") or 0)
        llm_calls += int(llm.get("calls") or 0)
        music_units += int(music.get("units") or 0)
        music_runs += int(music.get("runs") or 0)
        music_elapsed += int(music.get("elapsed_ms") or 0)
        music_duration += float(music.get("duration_sec") or 0)
    return {
        "llm_tokens": llm_total,
        "llm_prompt_tokens": llm_prompt,
        "llm_completion_tokens": llm_completion,
        "llm_calls": llm_calls,
        "music_units": music_units,
        "music_runs": music_runs,
        "music_elapsed_ms": music_elapsed,
        "music_duration_sec": round(music_duration, 1),
    }


def read_compute_ledger(limit: int = 5000) -> List[dict]:
    return _read_jsonl(COMPUTE_LEDGER_PATH, limit=limit)


def recent_compute(limit: int = 30) -> List[dict]:
    rows = read_compute_ledger(limit=max(limit, 200))
    return list(reversed(rows[-limit:]))
