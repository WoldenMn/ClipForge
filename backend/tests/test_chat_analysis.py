from pathlib import Path

from clipforge.pipeline.chat_analysis import (
    ChatMsg,
    compute_chat_scores,
    has_reaction_marker,
    parse_chat_json,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chat_sample.json"


def test_parse_chat_json():
    msgs = parse_chat_json(FIXTURE)
    assert len(msgs) == 6
    assert msgs[0].t == 5.0 and msgs[0].has_emote is False
    assert msgs[1].has_emote is True


def test_burst_window_scores_highest():
    msgs = parse_chat_json(FIXTURE)
    scores = compute_chat_scores(msgs, duration_s=120.0, window_s=10.0, hop_s=5.0)
    best_t = max(scores, key=lambda p: p[1])[0]
    assert 55.0 <= best_t <= 65.0
    assert all(0.0 <= s <= 1.0 for _, s in scores)


def test_empty_messages_score_zero():
    scores = compute_chat_scores([], duration_s=120.0, window_s=10.0, hop_s=5.0)
    assert len(scores) > 0
    assert all(s == 0.0 for _, s in scores)


def test_uniform_chat_gives_no_peak():
    msgs = [ChatMsg(t=float(w * 10 + k * 3), has_emote=False)
            for w in range(20) for k in range(3)]
    scores = compute_chat_scores(msgs, duration_s=200.0, window_s=10.0, hop_s=5.0)
    assert len({round(s, 4) for _, s in scores}) == 1


def test_acceleration_beats_equal_rate():
    steady = [ChatMsg(t=float(i) * 2.0, has_emote=False) for i in range(60)]
    ramp = steady + [ChatMsg(t=80.0 + i * 0.2, has_emote=False) for i in range(25)]
    scores = dict(compute_chat_scores(ramp, duration_s=160.0, window_s=10.0, hop_s=5.0))
    assert scores[80.0] > scores[40.0]


def test_reaction_markers():
    assert has_reaction_marker("KEKW") is True
    assert has_reaction_marker("c'est kekw ce truc") is True
    assert has_reaction_marker("PTDR le clip") is True
    assert has_reaction_marker("mdrrr") is True
    assert has_reaction_marker("il faut clipper ca") is True
    assert has_reaction_marker("😂😂") is True
    assert has_reaction_marker("bonsoir tout le monde") is False
    assert has_reaction_marker("") is False
    assert has_reaction_marker("eclipse totale") is False
    assert has_reaction_marker("lolita") is False


def test_parse_chat_json_detects_markers(tmp_path):
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"comments": [
        {"content_offset_seconds": 1.0, "message": {"body": "KEKW", "fragments": []}},
        {"content_offset_seconds": 2.0, "message": {"body": "salut", "fragments": []}},
    ]}), encoding="utf-8")
    msgs = parse_chat_json(p)
    assert msgs[0].has_emote is True
    assert msgs[1].has_emote is False
