"""
從 LM Studio / OpenAI 相容回覆裡撈出 JSON。
推理模型常把答案包在 ```json 或 reasoning_content 裡，且字串內可能有真實換行。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def message_text(message: dict) -> str:
    """合併 content + reasoning_content，避免只看其中一邊而漏掉 JSON。"""
    parts = []
    for key in ("content", "reasoning_content"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return "\n\n".join(parts)


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    fence = re.search(r"```(?:json|JSON)?\s*([\s\S]*?)```", cleaned)
    if fence:
        return fence.group(1).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.strip().lower().startswith("json")
        )
    return cleaned.strip()


def _brace_objects(text: str) -> list[str]:
    """依大括號配對抽出所有頂層 {...}（可含字串內換行）。"""
    out: list[str] = []
    start = None
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start : i + 1])
                start = None
    return out


def _repair_newlines_in_strings(s: str) -> str:
    """只把「字串值裡面」的真實換行轉成 \\n，保留結構換行。"""
    out: list[str] = []
    in_str = False
    escape = False
    for ch in s:
        if in_str:
            if escape:
                escape = False
                out.append(ch)
            elif ch == "\\":
                escape = True
                out.append(ch)
            elif ch == '"':
                in_str = False
                out.append(ch)
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                continue
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _loads_lenient(cand: str) -> Optional[Any]:
    for attempt in (cand, _repair_newlines_in_strings(cand)):
        try:
            return json.loads(attempt)
        except Exception:
            continue
    # 尾端多餘逗號
    fixed = re.sub(r",\s*([}\]])", r"\1", _repair_newlines_in_strings(cand))
    try:
        return json.loads(fixed)
    except Exception:
        return None


def extract_json_objects(text: str) -> list[Any]:
    """由後往前回傳所有能 parse 的 JSON 物件（最終答案通常在最後）。"""
    if not text:
        return []
    cleaned = _strip_fences(text)
    candidates = [cleaned]
    candidates.extend(_brace_objects(cleaned))
    # 相容舊的單層 regex
    candidates.extend(re.findall(r"\{[^{}]*\}", cleaned, flags=re.DOTALL))

    seen = set()
    parsed_list: list[Any] = []
    for cand in reversed(candidates):
        key = cand.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        obj = _loads_lenient(key)
        if isinstance(obj, dict):
            parsed_list.append(obj)
    return parsed_list
