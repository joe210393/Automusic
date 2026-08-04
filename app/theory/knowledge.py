"""
樂理知識庫

所有作曲引擎（和弦挑選、旋律生成、LM Studio 提示詞）在製作音樂之前，
都會先透過這個模組讀取 theory_db.json 裡的樂理規則。

想調整作曲風格時，直接編輯 theory_db.json 即可，不需要改程式。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

THEORY_DB_PATH = Path(__file__).parent / "theory_db.json"


@lru_cache(maxsize=1)
def load_theory() -> dict:
    """讀取樂理資料庫（快取，整個程式共用一份）。"""
    with open(THEORY_DB_PATH, encoding="utf-8") as f:
        return json.load(f)


def melody_rules() -> dict:
    return load_theory()["melody_rules"]


def get_style(name: Optional[str]) -> Optional[dict]:
    """取得風格定義；name 為 None、'auto' 或不存在時回傳 None（代表自動）。"""
    if not name or name == "auto":
        return None
    return load_theory().get("styles", {}).get(name)


def list_styles() -> dict:
    """回傳所有風格（給前端下拉選單用）。"""
    return load_theory().get("styles", {})


def pick_ensemble(mood: Optional[str], rng) -> dict:
    """
    挑一組樂團編制（主奏樂器＋和聲樂器＋裝飾聲部＋是否用鼓）。
    有 mood 時優先挑感覺相符的編制，讓「自動」模式每次都換不同樂團。
    """
    ensembles = load_theory().get("ensembles", [])
    candidates = [e for e in ensembles if mood and mood in e.get("moods", [])] or ensembles
    return rng.choice(candidates)


def _fit_to_bars(degrees: List[str], num_bars: int) -> List[str]:
    """
    把進行重複/裁切到指定小節數。

    不在這裡強制終止式：整首歌最後的 V→I 收尾由 generate_midi 在
    歌曲層級處理（尾奏前放 V、尾奏放 I），這樣段落中間的經典進行
    才能保持原味（例如 I-V-vi-IV 不會被改成 I-V-V-I）。
    """
    fitted = []
    while len(fitted) < num_bars:
        fitted.extend(degrees)
    return fitted[:num_bars]


def pick_progression_for_mood(
    mood: str,
    num_bars: int,
    rng=None,
) -> dict:
    """
    依感覺（mood）從資料庫挑一組和弦進行。

    mood 可以是 bright / warm / calm / sad / emotional / energetic / simple / jazzy。
    回傳 {"name": 進行名稱, "degrees": 貼合小節數的和弦列表}。
    """
    import random as _random

    rng = rng or _random
    progressions = load_theory()["progressions"]
    candidates = [p for p in progressions if mood in p.get("moods", [])]
    if not candidates:
        candidates = progressions

    weights = [p.get("weight", 1) for p in candidates]
    chosen = rng.choices(candidates, weights=weights)[0]
    return {
        "name": chosen["name"],
        "degrees": _fit_to_bars(chosen["degrees"], num_bars),
    }


def best_progression_for_melody(
    notes: list,
    key: str,
    bpm: float,
    num_bars: int,
    time_signature: tuple = (4, 4),
) -> dict:
    """
    幫既有旋律配和弦：把資料庫裡每一組經典進行逐一跟旋律比對，
    以「旋律音落在和弦內音上的總時值」評分，挑總分最高的進行。

    比起逐小節貪婪挑和弦，整組進行天生就有起承轉合，聽起來更像一首歌。
    回傳 {"name": 進行名稱, "degrees": 和弦列表}。
    """
    from app.arrange.chords import get_chord_pitch_classes

    beats_per_second = bpm / 60.0
    bar_duration = time_signature[0] / beats_per_second

    # 預先統計每小節各音級的總時值
    bar_pc_durations = []
    for bar in range(num_bars):
        bar_start = bar * bar_duration
        bar_end = bar_start + bar_duration
        pc_duration = {}
        for n in notes:
            overlap = min(n.get("end", 0), bar_end) - max(n.get("start", 0), bar_start)
            if overlap > 0:
                pc = n.get("midi", 60) % 12
                pc_duration[pc] = pc_duration.get(pc, 0.0) + overlap
        bar_pc_durations.append(pc_duration)

    best_name, best_degrees, best_score = None, None, float("-inf")
    for prog in load_theory()["progressions"]:
        degrees = _fit_to_bars(prog["degrees"], num_bars)
        score = 0.0
        for bar, degree in enumerate(degrees):
            tones = get_chord_pitch_classes(key, degree)
            pc_duration = bar_pc_durations[bar]
            total = sum(pc_duration.values()) or 1.0
            hit = sum(dur for pc, dur in pc_duration.items() if pc in tones)
            score += hit / total
        # 資料庫權重當成小加分，同分時偏好常用進行
        score += prog.get("weight", 1) * 0.02
        if score > best_score:
            best_name, best_degrees, best_score = prog["name"], degrees, score

    return {"name": best_name, "degrees": best_degrees}


def theory_prompt_text(mood: Optional[str] = None, compact: bool = False) -> str:
    """
    把樂理資料庫整理成文字，注入 LLM 的 system prompt，
    讓模型在建議和弦之前先「讀過樂理」。

    compact=True 時輸出精簡版：推理模型看到冗長規則會逐條分析、
    思考時間暴增，精簡版能把回應時間控制在可接受範圍。
    """
    db = load_theory()

    if compact:
        progs = "；".join(
            f"{'-'.join(p['degrees'])}（{p['name']}）" for p in db["progressions"]
        )
        return (
            f"經典和弦進行：{progs}。\n"
            "守則：從上面挑一組最貼合旋律感覺的即可，倒數兩小節建議 V→I 收尾。"
        )

    lines = ["【樂理資料庫】建議和弦前請先遵守以下樂理：", "", "◆ 經典和弦進行（優先選用或變化）："]
    for p in db["progressions"]:
        mood_tag = "、".join(p.get("moods", []))
        lines.append(f"  - {p['name']}：{'-'.join(p['degrees'])}（適合：{mood_tag}）")

    lines.append("")
    lines.append("◆ 終止式：")
    for c in db["cadences"].values():
        lines.append(f"  - {'-'.join(c['degrees'])}：{c['note']}")

    lines.append("")
    lines.append("◆ 作曲守則：")
    for rule in db["llm_prompt_rules"]:
        lines.append(f"  - {rule}")

    if mood:
        lines.append("")
        lines.append(f"◆ 這段旋律的感覺偏「{mood}」，請優先挑選對應此感覺的進行。")

    return "\n".join(lines)
