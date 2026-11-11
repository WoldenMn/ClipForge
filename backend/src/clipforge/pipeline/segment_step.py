"""Etape de segmentation du pipeline, extraite de steps.py.

Les imports lourds (segmenter, thumbs, previews...) restent DANS step_segment :
le process parent importe ce module sans payer leur chargement.
"""
from pathlib import Path

from clipforge import db as dbmod
from clipforge.text import join_words


def step_segment(conn, vod_id, ctx):
    from clipforge import config
    from clipforge.pipeline import previews, segmenter, thumbs

    vod = dbmod.get_vod(conn, vod_id)
    words = [segmenter.Word(start=r["start_s"], end=r["end_s"], text=r["text"])
             for r in conn.execute(
                 "SELECT * FROM words WHERE vod_id=? ORDER BY idx", (vod_id,))]
    signals = {}
    for kind in ("audio", "chat"):
        signals[kind] = [(r["t_s"], r["score"]) for r in conn.execute(
            "SELECT t_s, score FROM signals WHERE vod_id=? AND kind=? ORDER BY t_s",
            (vod_id, kind))]
    signals["speech"] = segmenter.compute_speech_scores(
        words, duration_s=vod["duration_s"] or 0.0)
    weights = {k: float(dbmod.get_setting(conn, f"weight_{k}") or 1.0)
               for k in ("audio", "chat", "speech")}
    combined = segmenter.combine_signals(signals, weights)
    cands = segmenter.find_candidates(combined, words,
                                      duration_s=vod["duration_s"] or None)

    def dominant(c):
        best_kind, best_val = None, 0.0
        for kind, series in signals.items():
            vals = [s for t, s in series if c.start_s - 10.0 <= t <= c.end_s]
            v = (sum(vals) / len(vals)) * weights.get(kind, 1.0) if vals else 0.0
            if v > best_val:
                best_kind, best_val = kind, v
        return best_kind

    def hook_text(c) -> str:
        texts = [r["text"] for r in conn.execute(
            "SELECT text FROM words WHERE vod_id=? AND start_s>=? AND end_s<=? "
            "ORDER BY idx LIMIT 12", (vod_id, c.start_s, c.end_s))]
        return join_words(texts)[:60]

    conn.execute("DELETE FROM segments WHERE vod_id=?", (vod_id,))
    for rank, c in enumerate(cands, start=1):
        conn.execute(
            "INSERT INTO segments (vod_id, start_s, end_s, score, rank, top_signal, "
            "signals_count, hook_text) VALUES (?,?,?,?,?,?,?,?)",
            (vod_id, c.start_s, c.end_s, c.score, rank, dominant(c),
             segmenter.count_convergent(signals, c.peak_s), hook_text(c)))
    conn.commit()
    source = Path(vod["source_path"])
    for c in cands:
        # Vignette et apercu partagent le meme instant : la carte affiche
        # l'image fixe, puis l'extrait se substitue a elle au survol.
        t = c.start_s + thumbs.CANDIDATE_LEAD_S
        thumbs.try_thumb(config.JOBS_DIR, vod_id, source, t)
        previews.try_preview(config.JOBS_DIR, vod_id, source, t)
