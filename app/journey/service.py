"""
旅程編曲管線：直接呼叫現有引擎函式（不改引擎實作）。
"""
from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from app.content.loader import load_destination, resolve_engine_style, story_to_keywords
from app.journey import store


def _step(meta: dict, label: str) -> None:
    steps = meta.setdefault("compose_steps", [])
    if label not in steps:
        steps.append(label)


def primary_sound_path(meta: dict) -> Optional[Path]:
    sounds = meta.get("sounds") or []
    if not sounds:
        return None
    # 優先第一個槽，否則任意
    jid = meta["id"]
    for s in sounds:
        p = store.sounds_dir(jid) / s["filename"]
        if p.exists():
            return p
    return None


def run_compose(journey_id: str) -> dict:
    """素材→旋律→歌詞→伴奏預覽。"""
    meta = store.load_meta(journey_id)
    dest = load_destination(meta.get("destination") or "suao")
    if not dest:
        raise RuntimeError("找不到目的地內容包")

    sound = primary_sound_path(meta)
    if not sound:
        raise RuntimeError("請先收集至少一個旅行聲音")

    mood_id = meta.get("mood_id")
    engine_style = meta.get("engine_style") or resolve_engine_style(dest, mood_id or "")
    meta["engine_style"] = engine_style
    meta["status"] = "composing"
    meta["error"] = None
    meta["compose_steps"] = []
    store.save_meta(journey_id, meta)

    _step(meta, "整理旅行聲音")
    store.save_meta(journey_id, meta)

    # 1) 旋律
    from app.melody.from_audio import generate_melody_from_material

    _step(meta, "創作旋律")
    store.save_meta(journey_id, meta)
    seed = secrets.randbelow(10**9)
    melody = generate_melody_from_material(
        str(sound), style=engine_style, seed=seed
    )
    meta["notes"] = melody["notes"]
    meta["bpm"] = melody["bpm"]
    meta["key"] = melody["key"]
    meta["chords"] = melody.get("chords")
    meta["material"] = melody.get("material") or {}
    meta["compose_seed"] = seed
    style_id = (melody.get("material") or {}).get("style_id")
    if style_id:
        meta["engine_style"] = style_id
        engine_style = style_id
    store.save_meta(journey_id, meta)

    # 2) 歌詞
    _step(meta, "完成歌詞")
    store.save_meta(journey_id, meta)
    story = {
        "keywords": meta.get("keywords") or [],
        "place": meta.get("place") or "",
        "companions": meta.get("companions") or "",
        "feeling": meta.get("feeling") or "",
        "memory": meta.get("memory") or "",
    }
    keywords = story_to_keywords(story, dest)
    if not keywords:
        raise RuntimeError("請先填寫歌詞關鍵字")

    lyrics = _generate_lyrics(keywords, engine_style)
    meta["lyrics"] = lyrics
    if lyrics.get("title") and not str(meta.get("title") or "").strip():
        meta["title"] = str(lyrics["title"]).strip()[:40]
    store.save_meta(journey_id, meta)

    # 3) 伴奏預覽（歌曲 A：簡單編曲，無人聲）
    _step(meta, "編排伴奏")
    store.save_meta(journey_id, meta)
    preview_path = _render_and_save_tier(meta, "a")
    meta["preview_file"] = Path(preview_path).name
    meta["song_a_file"] = meta["preview_file"]
    meta["status"] = "preview"
    _step(meta, "旅行歌曲誕生了")
    store.save_meta(journey_id, meta)
    return meta


def _set_finalize_progress(meta: dict, pct: int, label: str) -> None:
    meta["finalize_progress"] = {
        "pct": max(0, min(100, int(pct))),
        "label": label,
    }
    _step(meta, label)
    store.save_meta(meta["id"], meta)


def run_finalize(journey_id: str) -> dict:
    """向後相容：等同 AI 試聽版成品。"""
    return run_finalize_ai(journey_id, full=False)


# 歌曲堆疊：同一組旋律／和弦／風格 seed，越往上編曲越完整（不是循環播放 A）
# A（預覽伴奏）→ A+（試聽：再編曲＋人聲）→ A++（完整：主副歌結構＋人聲）
ARRANGEMENT_TIERS = {
    "a": {
        "duration": 45,
        "melody_gain": 1.0,
        "label": "歌曲 A",
        "file": "song-a.mp3",
        "meta_key": "preview_file",
    },
    "a_plus": {
        "duration": 60,
        "melody_gain": 0.48,
        "label": "歌曲 A+",
        "file": "song-a-plus.mp3",
        "meta_key": "song_a_plus_file",
    },
    "a_plusplus": {
        "duration": 105,
        "melody_gain": 0.4,
        "label": "歌曲 A++",
        "file": "song-a-plusplus.mp3",
        "meta_key": "song_a_plusplus_file",
    },
}


def _arrangement_seed(meta: dict) -> int:
    """A / A+ / A++ 共用同一個編曲 seed，保證是同一首歌往上堆。"""
    raw = meta.get("compose_seed")
    if raw is not None:
        try:
            return int(raw) % (10**9)
        except Exception:
            pass
    return abs(hash(str(meta.get("id") or "") + "-arrangement")) % (10**9)


def _ensure_preview_path(meta: dict) -> Path:
    """保證有歌曲 A（預覽伴奏）。"""
    jid = meta["id"]
    name = meta.get("preview_file")
    if name:
        path = store.output_dir(jid) / name
        if path.is_file():
            return path
    return _render_and_save_tier(meta, "a")


def _render_and_save_tier(meta: dict, tier: str) -> Path:
    """依階層重新 MIDI 編曲並存檔（不循環舊 MP3）。"""
    if tier not in ARRANGEMENT_TIERS:
        raise ValueError(f"unknown arrangement tier: {tier}")
    cfg = ARRANGEMENT_TIERS[tier]
    rendered = _render_arrangement(
        meta,
        vocal_mode="preview",
        duration_seconds=int(cfg["duration"]),
        melody_gain=float(cfg["melody_gain"]),
        arrangement_seed=_arrangement_seed(meta),
    )
    dest = store.output_dir(meta["id"]) / cfg["file"]
    src = Path(rendered)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
        try:
            if src.name.startswith("journey_render_") or src.suffix == ".mp3":
                # compress_to_mp3 可能回傳暫存；清掉避免堆積
                if str(src).startswith(tempfile.gettempdir()):
                    src.unlink(missing_ok=True)
        except OSError:
            pass
    meta[cfg["meta_key"]] = cfg["file"]
    if tier == "a":
        meta["preview_file"] = cfg["file"]
        meta["song_a_file"] = cfg["file"]
    store.save_meta(meta["id"], meta)
    return dest


def _parse_seed_int(raw) -> Optional[int]:
    if raw is None:
        return None
    try:
        text = str(raw).split(",")[0].strip()
        return int(text)
    except Exception:
        return None


def _mix_ai_vocals_onto_tier(
    meta: dict,
    *,
    tier: str,
    out_path: Path,
) -> dict:
    """
    在進化後的編曲床（A+ / A++）上混入 DiffSinger 人聲。
    床是重新 generate_full_midi，不是循環歌曲 A。
    """
    import numpy as np
    import soundfile as sf
    import subprocess

    from app.main import compress_to_mp3
    from app.midi.generate_midi import compute_song_structure
    from app.voice import svs
    from app.voice.acestep import vocal_presence_score
    from app.voice.sing import apply_reverb
    from app.voice.singer_templates import (
        DIFFSINGER_NATIVE_MIDI,
        apply_arrangement_color,
        apply_template_color,
        get_template,
    )

    if not (svs.is_available() or svs.SVS_REMOTE_URLS):
        raise RuntimeError("AI 代唱引擎未就緒，請稍後再試")

    cfg = ARRANGEMENT_TIERS[tier]
    duration_sec = float(cfg["duration"])
    _set_finalize_progress(meta, 18, f"升級編曲為{cfg['label']}")
    bed_path = _render_and_save_tier(meta, tier)

    # 讀取編曲床；統一 44.1k stereo
    acc, sr = sf.read(str(bed_path))
    if acc.ndim == 1:
        acc = np.stack([acc, acc], axis=1)
    if int(sr) != 44100:
        resampled = Path(tempfile.mktemp(prefix="tier_bed_44k_", suffix=".wav"))
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(bed_path),
                "-ar", "44100", "-ac", "2",
                str(resampled),
            ]
        )
        acc, sr = sf.read(str(resampled))
        if acc.ndim == 1:
            acc = np.stack([acc, acc], axis=1)
        try:
            resampled.unlink(missing_ok=True)
        except OSError:
            pass

    singer_id = meta.get("ai_singer_id") or get_template(None)["id"]
    try:
        acc = apply_arrangement_color(acc, singer_id)
    except Exception as e:
        print(f"[journey] arrangement color skip: {e}")

    notes_list = [
        {
            "start": n["start"],
            "end": n["end"],
            "midi": n["midi"],
            "velocity": n.get("velocity", 90),
        }
        for n in (meta.get("notes") or [])
    ]
    lyrics = meta.get("lyrics") or {}
    lyrics_obj = {"verse": lyrics.get("verse"), "chorus": lyrics.get("chorus")}
    bpm = float(meta.get("bpm") or 100)
    structure = compute_song_structure(
        notes_list, bpm, target_seconds=int(round(duration_sec))
    )

    _set_finalize_progress(meta, 48, f"AI 在{cfg['label']}上唱歌")
    vocal = svs.build_svs_vocal_track(
        notes=notes_list,
        bpm=bpm,
        structure=structure,
        lyrics=lyrics_obj,
        total_samples=len(acc),
        speaker_midi=float(DIFFSINGER_NATIVE_MIDI),
        fs=44100,
    )
    if vocal is None:
        raise RuntimeError("無法依歌詞合成人聲")

    _set_finalize_progress(meta, 72, "套用選定音色")
    vocal = apply_template_color(vocal, singer_id, fs=44100)

    _set_finalize_progress(meta, 88, f"混音成{cfg['label']}")
    mix = _mix_vocal_into_acc(
        acc.astype(np.float64) * 0.82,
        vocal,
        apply_reverb,
        vocal_to_acc=1.5,
    )

    wav = tempfile.mktemp(prefix="journey_ai_sung_", suffix=".wav")
    sf.write(wav, mix, 44100)
    mp3 = compress_to_mp3(wav)
    final_src = mp3 or wav
    shutil.copyfile(final_src, out_path)
    for p in (final_src, wav):
        try:
            if Path(p).resolve() != out_path.resolve():
                os.unlink(p)
        except OSError:
            pass

    score = float(vocal_presence_score(out_path))
    return {
        "engine": f"{tier}_diffsinger",
        "tier": tier,
        "duration_sec": duration_sec,
        "vocal_score": round(score, 3),
        "path": out_path,
        "bed_file": cfg["file"],
    }


def run_finalize_ai(journey_id: str, *, full: bool = False) -> dict:
    """
    歌曲堆疊：
    - 試聽 A+：在歌曲 A 旋律地基上重新編曲，再加 AI 人聲
    - 完整 A++：再進化成主副歌完整編曲＋人聲（不是循環播放 A）
    """
    from app.voice.singer_templates import get_template, is_valid_singer_id
    from app.voice import acestep as _ace
    from app.voice import svs

    meta = store.load_meta(journey_id)
    if not meta.get("notes") or not meta.get("lyrics"):
        raise RuntimeError("請先完成創作")
    if not is_valid_singer_id(meta.get("ai_singer_id")):
        # 完整版升級：若試聽版已存在但缺歌手（舊旅程），退回預設音色
        if full and meta.get("final_file"):
            meta["ai_singer_id"] = get_template(None)["id"]
        else:
            raise RuntimeError("請先選擇 AI 歌手")

    prev_status = meta.get("status") or "done"
    meta["status"] = "finalizing"
    meta["error"] = None
    meta["compose_steps"] = []
    meta["finalize_progress"] = {"pct": 0, "label": "準備製作"}
    tpl = get_template(meta.get("ai_singer_id"))
    store.save_meta(journey_id, meta)
    _set_finalize_progress(meta, 8, "準備製作")

    _ensure_preview_path(meta)
    meta["song_a_file"] = meta.get("preview_file")
    tier = "a_plusplus" if full else "a_plus"
    cfg = ARRANGEMENT_TIERS[tier]
    duration = float(cfg["duration"])
    out_name = "final-full.mp3" if full else "final.mp3"
    out = store.output_dir(journey_id) / out_name

    _set_finalize_progress(
        meta,
        12,
        f"從歌曲 A 升級為{cfg['label']}",
    )

    try:
        if svs.is_available() or svs.SVS_REMOTE_URLS:
            result = _mix_ai_vocals_onto_tier(meta, tier=tier, out_path=out)
            meta["vocal_score"] = result.get("vocal_score")
            meta["final_engine"] = result.get("engine") or f"{tier}_diffsinger"
            meta["arrangement_tier"] = tier
            meta["ace_duration"] = float(result.get("duration_sec") or duration)
        elif _ace.is_available():
            # 後備：先進化編曲床，再 ACE cover（仍不循環舊 A）
            _set_finalize_progress(meta, 18, f"升級編曲為{cfg['label']}")
            bed = _render_and_save_tier(meta, tier)
            _set_finalize_progress(meta, 35, f"AI 在{cfg['label']}上唱歌")
            seed = _parse_seed_int(meta.get("ace_seed")) if full else None
            ace = _ace.generate_to_file(
                lyrics=meta["lyrics"],
                bpm=float(meta.get("bpm") or 100),
                key=meta.get("key"),
                singer_id=meta.get("ai_singer_id"),
                engine_style=meta.get("engine_style"),
                duration_sec=duration,
                out_path=out,
                src_audio_path=bed,
                seed=seed,
                full_lyrics=full,
                fit_source_duration=False,
                progress=lambda pct, label: _set_finalize_progress(meta, pct, label),
            )
            meta["vocal_score"] = round(float(_ace.vocal_presence_score(ace.path)), 3)
            meta["final_engine"] = ace.engine or "acestep_cover"
            meta["arrangement_tier"] = tier
            if ace.seed:
                meta["ace_seed"] = ace.seed
            meta["ace_duration"] = float(ace.duration_sec or duration)
        else:
            meta["status"] = "done" if meta.get("final_file") else prev_status
            store.save_meta(journey_id, meta)
            raise RuntimeError("AI 唱歌引擎未就緒，請稍後再試")
    except Exception as e:
        print(f"[journey] AI finalize failed: {e}")
        meta = store.load_meta(journey_id)
        if meta.get("final_file"):
            meta["status"] = "done"
            meta["error"] = "AI 唱歌製作失敗，請稍後再試"
            meta["finalize_progress"] = None
            store.save_meta(journey_id, meta)
        raise RuntimeError("AI 唱歌製作失敗，請稍後再試") from e

    if full:
        meta["final_full_file"] = out_name
    else:
        meta["final_file"] = out_name
    meta["ace_full"] = bool(full)
    meta["status"] = "done"
    meta["share_public"] = True
    meta["ai_singer_label"] = tpl.get("label")
    meta["error"] = None
    _set_finalize_progress(meta, 100, "完成")
    return meta


def run_finalize_full(journey_id: str) -> dict:
    """付費升級：另存完整版（不覆蓋試聽 final.mp3）。"""
    return run_finalize_ai(journey_id, full=True)


def run_finalize_voice(journey_id: str) -> dict:
    """在已有 AI 版的前提下，另存聲紋版（不覆蓋 final_file）。"""
    meta = store.load_meta(journey_id)
    if not meta.get("notes") or not meta.get("lyrics"):
        raise RuntimeError("請先完成創作")
    if not meta.get("final_file"):
        raise RuntimeError("請先完成 AI 版本")
    consent = meta.get("voiceprint_consent") or {}
    if not consent.get("accepted"):
        raise RuntimeError("請先同意個資說明後再錄製自己的聲音")
    vp = store.load_voiceprint_manifest(journey_id)
    if len(vp.get("lines") or []) < 2:
        raise RuntimeError("請先錄下至少兩句你的聲音")

    meta["status"] = "finalizing"
    meta["error"] = None
    meta["compose_steps"] = []
    meta["finalize_progress"] = {"pct": 0, "label": "準備製作"}
    store.save_meta(journey_id, meta)
    _set_finalize_progress(meta, 8, "準備製作")

    final_path = _render_arrangement(meta, vocal_mode="voiceprint")
    out_name = "final-voice" + Path(final_path).suffix
    dest_file = store.output_dir(journey_id) / out_name
    shutil.copyfile(final_path, dest_file)
    meta["final_voice_file"] = out_name
    meta["status"] = "done"
    meta["share_public"] = True
    _set_finalize_progress(meta, 100, "完成")
    return meta


def _route_label(dest: dict, route_id: Optional[str]) -> str:
    for r in dest.get("routes", []):
        if r.get("id") == route_id:
            return r.get("label") or ""
    return ""


def _generate_lyrics(keywords: List[str], style: str) -> dict:
    """複用 LM／模板作詞邏輯（與 /generate-lyrics-ai 相同策略）。"""
    import requests
    from app.lyrics.ai_writer import build_lyrics_prompts, parse_lyrics_from_message

    # 旅行語氣：在 keywords 已含地點／故事
    system_prompt, user_prompt = build_lyrics_prompts(keywords, style)
    system_prompt = (
        "你正在為一趟真實的蘇澳／台灣海岸旅行寫專屬歌曲。\n"
        "歌詞要像遊客會帶走的紀念，溫暖、有畫面，不要科技詞彙。\n\n"
        + system_prompt
    )

    lm_urls = []
    if os.getenv("LM_STUDIO_URL"):
        lm_urls = [os.getenv("LM_STUDIO_URL")]
    elif os.getenv("LM_STUDIO_URLS"):
        lm_urls = [u.strip() for u in os.getenv("LM_STUDIO_URLS").split(",") if u.strip()]
    else:
        lm_urls = [
            "http://127.0.0.1:1234/v1/chat/completions",
            "https://tactually-venerable-inez.ngrok-free.dev/lm/v1/chat/completions",
        ]
    model = os.getenv("LM_STUDIO_MODEL", "google/gemma-4-31b-qat")

    for url in lm_urls:
        try:
            resp = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "ngrok-skip-browser-warning": "1",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 2048,
                },
                timeout=(4, 300),
            )
            if resp.status_code != 200:
                continue
            message = resp.json()["choices"][0]["message"]
            parsed = parse_lyrics_from_message(message)
            if parsed:
                return {
                    "title": parsed["title"],
                    "verse": parsed["verse"],
                    "chorus": parsed["chorus"],
                    "source": "lm_studio",
                }
        except Exception as e:
            print(f"[journey] lyrics LM fail ({url}): {e}")
            continue

    from app.lyrics.generator import generate_lyrics as gen_lyrics

    result = gen_lyrics(keywords, "溫暖")
    return {
        "title": (keywords[0] if keywords else "旅行") + "之歌",
        "verse": result["verse"],
        "chorus": result["chorus"],
        "source": "template",
    }


def _mix_vocal_into_acc(acc, vocal, apply_reverb, *, vocal_to_acc: float = 1.25):
    import numpy as np

    vocal = apply_reverb(vocal)
    acc_rms = float(np.sqrt(np.mean(acc ** 2)))
    voc_rms = float(np.sqrt(np.mean(vocal[vocal != 0] ** 2))) if np.any(vocal != 0) else 0.0
    if voc_rms > 1e-6:
        vocal = vocal * (acc_rms * float(vocal_to_acc) / voc_rms)
    mix = acc * 0.8
    mix[:, 0] += vocal
    mix[:, 1] += vocal
    m = float(np.max(np.abs(mix)))
    if m > 0.99:
        mix = mix * (0.99 / m)
    return mix


def _render_arrangement(
    meta: dict,
    *,
    use_voiceprint: bool = False,
    vocal_mode: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    melody_gain: Optional[float] = None,
    arrangement_seed: Optional[int] = None,
) -> str:
    """
    呼叫與 /render-audio 相同的核心路徑。
    vocal_mode:
      None / 搭配 use_voiceprint=False → 僅伴奏預覽
      "ai" → AI 歌手模板
      "voiceprint" → 使用者聲紋
    duration_seconds / melody_gain / arrangement_seed：
      歌曲 A→A+→A++ 堆疊時覆寫長度與音色密度（同一 seed 保證同一首歌）。
    """
    from app.main import (
        find_fluidsynth,
        find_soundfont,
        compress_to_mp3,
        _render_midi_via_remote,
        RENDER_REMOTE_URLS,
    )
    from app.midi.generate_midi import generate_full_midi, compute_song_structure
    from app.audio.soundfont_render import (
        can_render_acoustic_locally,
        render_midi_to_wav,
    )
    import soundfile as sf
    import numpy as np

    if vocal_mode is None:
        vocal_mode = "voiceprint" if use_voiceprint else "preview"

    lyrics = meta["lyrics"]
    notes = meta["notes"]
    bpm = float(meta["bpm"])
    key = meta["key"]
    style = meta.get("engine_style")
    seed = (
        int(arrangement_seed)
        if arrangement_seed is not None
        else abs(hash(meta["id"] + f"-{vocal_mode}")) % (10**9)
    )
    duration = int(duration_seconds) if duration_seconds is not None else 60
    # AI 版：完整編曲＋主旋律（暫不走 DiffSinger，避免鬼聲）
    # voiceprint 版：關主旋律 MIDI，改混入人聲
    with_user_vocals = vocal_mode == "voiceprint"
    is_ai_final = vocal_mode == "ai"
    if melody_gain is None:
        melody_gain = 0.0 if with_user_vocals else 1.0

    fluidsynth_bin = find_fluidsynth()
    if not fluidsynth_bin:
        raise RuntimeError("製作服務暫時忙碌，請稍後再試")
    if not find_soundfont():
        raise RuntimeError("製作服務暫時忙碌，請稍後再試")

    notes_list = [
        {"start": n["start"], "end": n["end"], "midi": n["midi"], "velocity": n.get("velocity", 90)}
        for n in notes
    ]
    lyrics_obj = {"verse": lyrics["verse"], "chorus": lyrics["chorus"]}

    if is_ai_final or with_user_vocals:
        _set_finalize_progress(meta, 25, "編排伴奏")

    midi_path = generate_full_midi(
        notes=notes_list,
        bpm=bpm,
        key=key,
        lyrics=lyrics_obj,
        chord_overrides=meta.get("chords"),
        seed=seed,
        melody_gain=float(melody_gain),
        style=style,
        duration_seconds=duration,
    )

    wav_path = tempfile.mktemp(prefix="journey_render_", suffix=".wav")
    use_lead = not with_user_vocals and float(melody_gain) > 0.05
    if not can_render_acoustic_locally() and RENDER_REMOTE_URLS:
        remote = _render_midi_via_remote(midi_path, use_lead_overlay=use_lead)
        if remote:
            shutil.copyfile(remote, wav_path)
            try:
                os.unlink(remote)
            except OSError:
                pass
        else:
            render_midi_to_wav(fluidsynth_bin, midi_path, wav_path, use_lead_overlay=use_lead)
    else:
        render_midi_to_wav(fluidsynth_bin, midi_path, wav_path, use_lead_overlay=use_lead)

    if is_ai_final:
        from app.voice.singer_templates import apply_arrangement_color, get_template

        _set_finalize_progress(meta, 55, "套用演奏風格")
        singer_id = meta.get("ai_singer_id") or get_template(None)["id"]
        try:
            audio, sr = sf.read(wav_path)
            colored = apply_arrangement_color(audio, singer_id)
            sf.write(wav_path, colored, sr)
        except Exception as e:
            print(f"[journey] arrangement color skip: {e}")
        _set_finalize_progress(meta, 85, "輸出成品")
        mp3 = compress_to_mp3(wav_path)
        return mp3 or wav_path

    if not with_user_vocals:
        mp3 = compress_to_mp3(wav_path)
        return mp3 or wav_path

    from app.voice.sing import build_vocal_track, apply_reverb, load_mono
    from app.voice import neural_vc, svs
    from app.voice.singer_templates import DIFFSINGER_NATIVE_MIDI

    jid = meta["id"]
    vp_dir = store.voiceprint_dir(jid)
    manifest = store.load_voiceprint_manifest(jid)

    acc, _ = sf.read(wav_path)
    if acc.ndim == 1:
        acc = np.stack([acc, acc], axis=1)
    structure = compute_song_structure(notes_list, bpm, target_seconds=duration)

    _set_finalize_progress(meta, 40, "AI 底稿代唱")
    vocal = None
    speaker_midi = float(DIFFSINGER_NATIVE_MIDI)
    from app.voice.sing import estimate_speaker_midi
    est = estimate_speaker_midi(vp_dir, manifest)
    if est is not None:
        speaker_midi = float(max(58.0, min(70.0, est)))

    if svs.is_available() or svs.SVS_REMOTE_URLS:
        vocal = svs.build_svs_vocal_track(
            notes=notes_list,
            bpm=bpm,
            structure=structure,
            lyrics=lyrics_obj,
            total_samples=len(acc),
            speaker_midi=speaker_midi,
        )

    if vocal is None:
        vocal = build_vocal_track(
            notes=notes_list,
            bpm=bpm,
            structure=structure,
            voiceprint_dir=vp_dir,
            manifest=manifest,
            total_samples=len(acc),
            lyrics=lyrics_obj,
        )

    if vocal is None:
        raise RuntimeError("還無法唱出這首歌，請確認已錄完幾句聲音")

    _set_finalize_progress(meta, 60, "套用你的聲音")
    ref_path = neural_vc.build_reference_wav(vp_dir, manifest)
    if ref_path and (neural_vc.is_available() or neural_vc.VC_REMOTE_URLS):
        src_path = tempfile.mktemp(prefix="journey_vocal_", suffix=".wav")
        sf.write(src_path, vocal, 44100)
        converted = neural_vc.convert_voice(src_path, ref_path)
        if converted:
            v = load_mono(converted, 44100)
            if len(v) < len(vocal):
                v = np.pad(v, (0, len(vocal) - len(v)))
            vocal = v[: len(vocal)]

    _set_finalize_progress(meta, 80, "混音處理")
    mix = _mix_vocal_into_acc(acc, vocal, apply_reverb)

    _set_finalize_progress(meta, 92, "輸出成品")
    sung = tempfile.mktemp(prefix="journey_sung_", suffix=".wav")
    sf.write(sung, mix, 44100)
    mp3 = compress_to_mp3(sung)
    return mp3 or sung
