"""
歌詞韻律優化：短句化、句數對齊、補 Pre-Chorus。

目標：給 ACE 好唱的結構，避免長句硬塞。
45 秒建議：Intro → Verse 4 → Pre-Chorus 2 → Chorus 4 → Outro
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# 中文歌詞：每行建議字數（含標點前的實質字元）
_MIN_CHARS = 4
_MAX_CHARS = 12
_VERSE_LINES = 4
_PRE_LINES = 2
_CHORUS_LINES = 4


def _strip_punct(s: str) -> str:
    return re.sub(r"[，,。.!！？?、；;：:\s]+", "", s or "")


def _char_len(s: str) -> int:
    return len(_strip_punct(s))


def _split_lines(text: str) -> List[str]:
    if not text:
        return []
    # 先依換行，再把逗號／句號長句切開
    raw: List[str] = []
    for block in re.split(r"[\n\r]+", text.strip()):
        block = block.strip()
        if not block:
            continue
        parts = re.split(r"[，,。.!！？?；;]+", block)
        for p in parts:
            p = p.strip(" 　")
            if p:
                raw.append(p)
    return raw


def _break_long(line: str) -> List[str]:
    """過長句子對半切，盡量在中間附近。"""
    n = _char_len(line)
    if n <= _MAX_CHARS:
        return [line]
    # 用字元（含空白）長度切
    mid = max(_MIN_CHARS, len(line) // 2)
    # 往前找空白或自然斷點
    cut = mid
    for i in range(mid, _MIN_CHARS, -1):
        if line[i - 1] in "的了著過與和而":
            cut = i
            break
    left, right = line[:cut].strip(), line[cut:].strip()
    out: List[str] = []
    for part in (left, right):
        if not part:
            continue
        if _char_len(part) > _MAX_CHARS:
            out.extend(_break_long(part))
        else:
            out.append(part)
    return out or [line]


def _normalize_section(text: str, target: int) -> List[str]:
    lines: List[str] = []
    for ln in _split_lines(text):
        lines.extend(_break_long(ln))
    # 去掉過短碎片（併入下一句）
    merged: List[str] = []
    buf = ""
    for ln in lines:
        if _char_len(ln) < _MIN_CHARS and buf:
            buf = buf + ln
            merged.append(buf)
            buf = ""
        elif _char_len(ln) < _MIN_CHARS:
            buf = ln
        else:
            if buf:
                merged.append(buf + ln)
                buf = ""
            else:
                merged.append(ln)
    if buf:
        if merged:
            merged[-1] = merged[-1] + buf
        else:
            merged.append(buf)
    lines = merged

    if len(lines) > target:
        lines = lines[:target]
    while len(lines) < target and lines:
        # 不夠就重複最後一句變體（略改結尾感）
        lines.append(lines[-1])
    if not lines:
        lines = ["今天的故事"] * target
    return lines[:target]


def _synthesize_prechorus(verse_lines: List[str], chorus_lines: List[str]) -> List[str]:
    """沒有 pre-chorus 時，從主歌末／副歌首抽出張力過渡。"""
    bits: List[str] = []
    if len(verse_lines) >= 2:
        bits.append(verse_lines[-1])
    elif verse_lines:
        bits.append(verse_lines[0])
    if chorus_lines:
        # 副歌第一句當抬升預告，略縮短
        hook = chorus_lines[0]
        if _char_len(hook) > 8:
            hook = hook[:8]
        bits.append(hook)
    while len(bits) < _PRE_LINES:
        bits.append(bits[-1] if bits else "往前走")
    return bits[:_PRE_LINES]


def optimize_lyrics(lyrics: Optional[dict]) -> dict:
    """
    輸入／輸出：{title, verse, chorus, prechorus?}
    verse／chorus／prechorus 皆為換行分隔短句。
    """
    src = lyrics if isinstance(lyrics, dict) else {}
    title = str(src.get("title") or "旅行之歌").strip()[:20] or "旅行之歌"

    verse_lines = _normalize_section(str(src.get("verse") or ""), _VERSE_LINES)
    chorus_lines = _normalize_section(str(src.get("chorus") or ""), _CHORUS_LINES)

    pre_raw = src.get("prechorus") or src.get("pre_chorus") or src.get("bridge")
    if pre_raw and str(pre_raw).strip():
        pre_lines = _normalize_section(str(pre_raw), _PRE_LINES)
    else:
        pre_lines = _synthesize_prechorus(verse_lines, chorus_lines)

    return {
        "title": title,
        "verse": "\n".join(verse_lines),
        "prechorus": "\n".join(pre_lines),
        "chorus": "\n".join(chorus_lines),
    }
