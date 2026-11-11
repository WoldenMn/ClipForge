import itertools
import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

from clipforge.pipeline.normalize import rank_normalize

_EXACT_MARKERS = ("kekw", "lulw", "omegalul", "lul", "poggers", "pogchamp", "ptdr", "lol")
_PREFIX_MARKERS = ("clip", "mdr")
_EMOJI_MARKERS = ("😂", "💀", "🤣")
_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(_EXACT_MARKERS) + r")\b"
    r"|\b(?:" + "|".join(_PREFIX_MARKERS) + r")\w*",
    re.IGNORECASE,
)


def has_reaction_marker(text: str) -> bool:
    """Le message porte-t-il un marqueur de reaction (emote Twitch ou rire ecrit) ?"""
    return bool(_MARKER_RE.search(text)) or any(m in text for m in _EMOJI_MARKERS)


@dataclass
class ChatMsg:
    t: float
    has_emote: bool


def parse_chat_json(path: Path | str) -> list[ChatMsg]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    msgs = []
    for c in data.get("comments", []):
        off = c.get("content_offset_seconds")
        if off is None:
            continue
        body = (c.get("message") or {}).get("body") or ""
        frags = (c.get("message") or {}).get("fragments") or []
        has_emote = any(f.get("emoticon") for f in frags) or has_reaction_marker(body)
        msgs.append(ChatMsg(t=float(off), has_emote=has_emote))
    return sorted(msgs, key=lambda m: m.t)


def compute_chat_scores(msgs: list[ChatMsg], *, duration_s: float, window_s: float = 10.0,
                        hop_s: float = 5.0) -> list[tuple[float, float]]:
    """Score par rang = debit vs mediane + acceleration vs les 3 fenetres precedentes
    + bonus densite d'emotes."""
    ts = [m.t for m in msgs]
    windows: list[tuple[float, int, int]] = []
    for i in itertools.count():
        t = i * hop_s
        if t >= max(duration_s - window_s, 0) + hop_s:
            break
        lo, hi = bisect_left(ts, t), bisect_left(ts, t + window_s)
        in_win = msgs[lo:hi]
        emotes = sum(1 for m in in_win if m.has_emote)
        windows.append((t, len(in_win), emotes))
    nonzero = sorted(n for _, n, _ in windows if n)
    baseline = nonzero[len(nonzero) // 2] if nonzero else 1.0
    raw = []
    for i, (_t, n, emotes) in enumerate(windows):
        prev = [windows[j][1] for j in range(max(0, i - 3), i)]
        if not n:
            accel = 0.0
        elif not prev:
            accel = 1.0 / 3.0
        else:
            accel = min(3.0, n / max(sorted(prev)[len(prev) // 2], 1)) / 3.0
        rate = n / (4.0 * max(baseline, 1.0))
        emote_bonus = 0.4 * (emotes / n) if n else 0.0
        raw.append(0.5 * rate + 0.3 * accel + emote_bonus)
    return [(windows[i][0], s) for i, s in enumerate(rank_normalize(raw))]
