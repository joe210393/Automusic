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


def build_sound_trace(meta: dict, destination: Optional[dict] = None) -> dict:
    """
    給旅人看的「收音 → 歌曲」對照：說明原音不會直接疊進成品，
    但調性／速度／輪廓／動機有寫進旋律與後續生成參數。
    """
    dest = destination or load_destination(meta.get("destination") or "suao") or {}
    route = None
    for r in dest.get("routes") or []:
        if r.get("id") == meta.get("route_id"):
            route = r
            break
    label_by_slot = {}
    for t in (route or {}).get("soundTasks") or []:
        if t.get("id"):
            label_by_slot[str(t["id"])] = t.get("label") or t["id"]

    collected = []
    for s in meta.get("sounds") or []:
        if not isinstance(s, dict):
            continue
        slot = str(s.get("slot") or "")
        collected.append(
            {
                "slot": slot,
                "label": s.get("label") or label_by_slot.get(slot) or slot or "旅行聲音",
            }
        )

    mat = meta.get("material") if isinstance(meta.get("material"), dict) else {}
    mood_label = None
    for m in dest.get("moodStyles") or []:
        if m.get("id") == meta.get("mood_id"):
            mood_label = m.get("label")
            break

    effects = []
    if mat.get("mood"):
        effects.append({"key": "明暗感覺", "value": str(mat["mood"])})
    if meta.get("key") or mat.get("root"):
        effects.append(
            {
                "key": "調性／主音",
                "value": str(meta.get("key") or mat.get("root") or "—"),
            }
        )
    if meta.get("bpm") is not None or mat.get("bpm") is not None:
        bpm = meta.get("bpm") if meta.get("bpm") is not None else mat.get("bpm")
        effects.append({"key": "速度", "value": f"{int(round(float(bpm)))} BPM"})
    if mat.get("contour"):
        energy = mat.get("energy")
        contour = str(mat["contour"])
        if energy is not None:
            contour = f"{contour}（能量約 {energy} 音/秒）"
        effects.append({"key": "旋律輪廓", "value": contour})
    if mat.get("num_material_notes"):
        effects.append(
            {
                "key": "寫進旋律的動機",
                "value": f"從錄音抓到 {int(mat['num_material_notes'])} 個聲音事件，編成主旋律片段",
            }
        )
    if mat.get("progression"):
        effects.append({"key": "和弦走向", "value": str(mat["progression"])})
    if mat.get("style") or meta.get("engine_style"):
        effects.append(
            {
                "key": "編曲風格",
                "value": str(mat.get("style") or meta.get("engine_style")),
            }
        )
    if mood_label:
        effects.append({"key": "你選的感覺", "value": str(mood_label)})

    return {
        "collected": collected,
        "effects": effects,
        "headline": "你收集的聲音，這樣變成歌",
        "summary": (
            "現場原音不會整段疊進成品（避免變吵），"
            "但會分析出明暗、速度、輪廓與音高動機，寫進旋律；"
            "AI 唱歌會沿用這些參數與你的歌詞來完成整曲。"
        ),
        "into_final": [
            "旋律調性與速度（BPM／Key）",
            "由錄音輪廓推導的主旋律走向",
            "風格與感覺卡決定的編排語氣",
            "你的故事關鍵字寫成的歌詞",
        ],
    }


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
    meta["sound_trace"] = build_sound_trace(meta, dest)
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
    llm_usage = lyrics.pop("llm_usage", None)
    meta["lyrics"] = lyrics
    if lyrics.get("title") and not str(meta.get("title") or "").strip():
        meta["title"] = str(lyrics["title"]).strip()[:40]
    if llm_usage:
        try:
            from app.ops import metering as ops_metering

            ops_metering.record_llm_usage(
                meta,
                journey_id=journey_id,
                kind="lyrics",
                usage=llm_usage,
                model=llm_usage.get("model"),
                save=False,
            )
        except Exception as e:
            print(f"[journey] llm metering skip: {e}")
    store.save_meta(journey_id, meta)

    # 3) 伴奏預覽（無人聲／真實樂器 SoundFont）
    _step(meta, "編排真實樂器伴奏")
    store.save_meta(journey_id, meta)
    meta["arrange_seed"] = secrets.randbelow(10**9)
    preview_path = _render_arrangement(meta, use_voiceprint=False)
    out_name = Path(preview_path).name
    dest_file = store.output_dir(journey_id) / out_name
    shutil.copyfile(preview_path, dest_file)
    meta["preview_file"] = out_name
    meta["status"] = "preview"
    _step(meta, "旅行歌曲誕生了")
    store.save_meta(journey_id, meta)
    return meta


def run_remake_preview(journey_id: str) -> dict:
    """同一旋律／歌詞，只重抽真實樂器編曲（不重跑 ACE、不重作詞）。"""
    meta = store.load_meta(journey_id)
    if not meta.get("notes") or not meta.get("lyrics"):
        raise RuntimeError("請先完成創作")

    meta["arrange_seed"] = secrets.randbelow(10**9)
    meta["error"] = None
    _step(meta, "換一版真實樂器伴奏")
    store.save_meta(journey_id, meta)

    preview_path = _render_arrangement(meta, use_voiceprint=False)
    out_name = Path(preview_path).name
    dest_file = store.output_dir(journey_id) / out_name
    shutil.copyfile(preview_path, dest_file)
    meta["preview_file"] = out_name
    if meta.get("status") not in ("done", "finalizing"):
        meta["status"] = "preview"
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
    """向後相容：等同 AI 版成品。"""
    return run_finalize_ai(journey_id)


def run_finalize_ai(journey_id: str) -> dict:
    """用選定的 AI 歌手風格合成最終成品（優先 ACE-Step 整曲人聲；失敗則編曲＋主旋律）。"""
    from app.voice.singer_templates import get_template, is_valid_singer_id

    meta = store.load_meta(journey_id)
    if not meta.get("notes") or not meta.get("lyrics"):
        raise RuntimeError("請先完成創作")
    if not is_valid_singer_id(meta.get("ai_singer_id")):
        raise RuntimeError("請先選擇 AI 歌手")

    meta["status"] = "finalizing"
    meta["error"] = None
    meta["compose_steps"] = []
    meta["finalize_progress"] = {"pct": 0, "label": "準備製作"}
    tpl = get_template(meta.get("ai_singer_id"))
    store.save_meta(journey_id, meta)
    _set_finalize_progress(meta, 8, "準備製作")

    from app.voice import acestep as _ace

    if not _ace.is_available():
        raise RuntimeError("AI 唱歌引擎未連線，請確認本機 ACE-Step 已啟動後再試")

    out = store.output_dir(journey_id) / "final.mp3"

    def _prog(pct: int, label: str) -> None:
        _set_finalize_progress(meta, pct, label)

    ace_stats: dict = {}
    try:
        _ace.generate_to_file(
            lyrics=meta["lyrics"],
            bpm=float(meta.get("bpm") or 100),
            key=meta.get("key"),
            singer_id=meta.get("ai_singer_id"),
            engine_style=meta.get("engine_style"),
            duration_sec=float(_ace.ACESTEP_DURATION_SEC or 45.0),
            out_path=out,
            progress=_prog,
            stats=ace_stats,
            material=meta.get("material") if isinstance(meta.get("material"), dict) else None,
        )
    except Exception as e:
        print(f"[journey] ACE-Step failed: {e}")
        raise RuntimeError("AI 唱歌製作失敗，請稍後再試") from e

    if ace_stats:
        meta["ace_params"] = {
            "model": ace_stats.get("model"),
            "shift": ace_stats.get("shift"),
            "thinking": ace_stats.get("thinking"),
            "thinking_effective": ace_stats.get("thinking_effective"),
            "loaded_lm_model": ace_stats.get("loaded_lm_model"),
            "inference_steps": ace_stats.get("inference_steps"),
            "production_caption": ace_stats.get("production_caption"),
            "caption_chars": ace_stats.get("caption_chars"),
            "seed": ace_stats.get("seed"),
            "elapsed_ms": ace_stats.get("elapsed_ms"),
        }
        try:
            from app.ops import metering as ops_metering

            ops_metering.record_music_usage(
                meta,
                journey_id=journey_id,
                kind="ai_finalize",
                duration_sec=float(ace_stats.get("duration_sec") or _ace.ACESTEP_DURATION_SEC or 45),
                inference_steps=int(ace_stats.get("inference_steps") or 8),
                elapsed_ms=int(ace_stats.get("elapsed_ms") or 0),
                engine=str(ace_stats.get("engine") or "acestep"),
                model=ace_stats.get("model"),
                via=ace_stats.get("via"),
                save=False,
            )
        except Exception as e:
            print(f"[journey] music metering skip: {e}")

    meta["final_file"] = "final.mp3"
    meta["final_engine"] = "acestep"
    meta["status"] = "done"
    meta["share_public"] = True
    meta["ai_singer_label"] = tpl.get("label")
    _set_finalize_progress(meta, 100, "完成")
    return meta


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
            payload = resp.json()
            message = payload["choices"][0]["message"]
            parsed = parse_lyrics_from_message(message)
            if parsed:
                from app.ops.metering import extract_openai_usage

                usage = extract_openai_usage(payload) or {}
                out = {
                    "title": parsed["title"],
                    "verse": parsed["verse"],
                    "chorus": parsed["chorus"],
                    "source": "lm_studio",
                }
                if usage:
                    out["llm_usage"] = {**usage, "model": model}
                return out
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


def _mix_vocal_into_acc(acc, vocal, apply_reverb):
    import numpy as np

    vocal = apply_reverb(vocal)
    acc_rms = float(np.sqrt(np.mean(acc ** 2)))
    voc_rms = float(np.sqrt(np.mean(vocal[vocal != 0] ** 2))) if np.any(vocal != 0) else 0.0
    if voc_rms > 1e-6:
        vocal = vocal * (acc_rms * 1.25 / voc_rms)
    mix = acc * 0.8
    mix[:, 0] += vocal
    mix[:, 1] += vocal
    m = float(np.max(np.abs(mix)))
    if m > 0.99:
        mix = mix * (0.99 / m)
    return mix


def _render_arrangement(meta: dict, *, use_voiceprint: bool = False, vocal_mode: Optional[str] = None) -> str:
    """
    呼叫與 /render-audio 相同的核心路徑。
    vocal_mode:
      None / 搭配 use_voiceprint=False → 僅伴奏預覽
      "ai" → AI 歌手模板
      "voiceprint" → 使用者聲紋
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
    if meta.get("arrange_seed") is not None:
        seed = int(meta["arrange_seed"]) % (10**9)
    else:
        seed = abs(hash(meta["id"] + f"-{vocal_mode}")) % (10**9)
    duration = 60
    # AI 版：完整編曲＋主旋律（暫不走 DiffSinger，避免鬼聲）
    # voiceprint 版：關主旋律 MIDI，改混入人聲
    with_user_vocals = vocal_mode == "voiceprint"
    is_ai_final = vocal_mode == "ai"

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
        melody_gain=0.0 if with_user_vocals else 1.0,
        style=style,
        duration_seconds=duration,
    )

    wav_path = tempfile.mktemp(prefix="journey_render_", suffix=".wav")
    use_lead = not with_user_vocals
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
