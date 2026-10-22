"""
AI 成品排隊：本機 ACE 一次只能做一首，所以這裡依先來後到一首一首送。

等待時間用最近幾首實際完成的時間估算；還沒有紀錄時用 MUSIC_QUEUE_SONG_SEC。
排隊只存在記憶體，伺服器重啟後排隊中的旅程會被標成中斷，請遊客再按一次。
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

DEFAULT_SONG_SEC = float(os.getenv("MUSIC_QUEUE_SONG_SEC", "210"))

_cond = threading.Condition()
_waiting: List[Tuple[str, Callable[[], None]]] = []
_running: Optional[Dict[str, float]] = None
_recent = deque(maxlen=6)
_worker: Optional[threading.Thread] = None


def _song_sec() -> float:
    if not _recent:
        return DEFAULT_SONG_SEC
    return max(60.0, sum(_recent) / len(_recent))


def _running_left(now: float, song: float) -> float:
    if not _running:
        return 0.0
    return max(20.0, song - (now - _running["started"]))


def _ensure_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = threading.Thread(target=_loop, name="music-queue", daemon=True)
    _worker.start()


def _loop() -> None:
    global _running
    while True:
        with _cond:
            while not _waiting:
                _cond.wait()
            job_id, fn = _waiting.pop(0)
            _running = {"id": job_id, "started": time.time()}
        started = time.time()
        ok = False
        try:
            fn()
            ok = True
        except Exception as e:
            print(f"[music-queue] job {job_id} failed: {e}")
        finally:
            with _cond:
                if ok:
                    _recent.append(time.time() - started)
                _running = None


def enqueue(job_id: str, fn: Callable[[], None]) -> bool:
    """加入排隊；同一旅程已在排或在做就不重複加入。"""
    with _cond:
        if contains(job_id):
            return False
        _waiting.append((job_id, fn))
        _ensure_worker()
        _cond.notify()
        return True


def contains(job_id: str) -> bool:
    with _cond:
        if _running and _running.get("id") == job_id:
            return True
        return any(j == job_id for j, _ in _waiting)


def cancel(job_id: str) -> bool:
    """只能取消還在排隊的；已經開始做的無法中途停下 ACE。"""
    with _cond:
        for i, (j, _) in enumerate(_waiting):
            if j == job_id:
                _waiting.pop(i)
                return True
        return False


def status(job_id: Optional[str] = None) -> Dict[str, object]:
    with _cond:
        now = time.time()
        song = _song_sec()
        left = _running_left(now, song)
        ids = [j for j, _ in _waiting]
        info: Dict[str, object] = {
            "running": 1 if _running else 0,
            "waiting": len(ids),
            "song_sec": round(song),
            "eta_new_sec": round(left + len(ids) * song + song),
        }
        if not job_id:
            return info
        if _running and _running.get("id") == job_id:
            info.update({"state": "running", "ahead": 0, "position": 0, "eta_done_sec": round(left)})
        elif job_id in ids:
            idx = ids.index(job_id)
            ahead = idx + (1 if _running else 0)
            start_in = left + idx * song
            info.update(
                {
                    "state": "waiting",
                    "ahead": ahead,
                    "position": ahead + 1,
                    "eta_start_sec": round(start_in),
                    "eta_done_sec": round(start_in + song),
                }
            )
        else:
            info["state"] = None
        return info
