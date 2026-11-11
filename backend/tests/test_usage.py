"""Journal d'usage : ce que l'utilisateur fait des candidats proposes."""
from clipforge import db as dbmod
from clipforge import usage


def test_record_stores_an_event_with_its_context(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    usage.record(conn, "clip_exported", vod_id=vod_id, segment_id=7,
                 rank=3, top_signal="chat")
    row = conn.execute("SELECT * FROM usage_events").fetchone()
    assert row["kind"] == "clip_exported"
    assert row["vod_id"] == vod_id
    assert row["segment_id"] == 7
    assert row["rank"] == 3
    assert row["top_signal"] == "chat"
    assert row["created_at"]


def test_record_keeps_bound_shifts_signed(conn):
    """Le SENS du decalage porte l'information : trop tot vs trop tard."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    usage.record(conn, "bounds_adjusted", vod_id=vod_id, segment_id=1,
                 start_shift_s=-4.5, end_shift_s=2.0)
    row = conn.execute("SELECT * FROM usage_events").fetchone()
    assert row["start_shift_s"] == -4.5
    assert row["end_shift_s"] == 2.0


def test_a_broken_journal_never_breaks_what_it_observes(conn, caplog):
    """Exception documentee a la regle « jamais avaler une erreur » : une
    observation ratee ne doit pas faire tomber un export qui, lui, a reussi."""
    conn.execute("DROP TABLE usage_events")
    conn.commit()
    usage.record(conn, "clip_exported", vod_id=1)  # ne leve pas
    assert "usage" in caplog.text.lower()


def test_events_survive_a_new_segmentation(conn):
    """Les segments sont supprimes et recrees a chaque analyse : l'historique
    d'usage ne doit PAS partir avec eux, sinon la mesure se vide toute seule."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("INSERT INTO segments (vod_id, start_s, end_s, score) VALUES (?,?,?,?)",
                 (vod_id, 0.0, 10.0, 1.0))
    seg_id = conn.execute("SELECT id FROM segments").fetchone()["id"]
    conn.commit()
    usage.record(conn, "clip_exported", vod_id=vod_id, segment_id=seg_id)
    conn.execute("DELETE FROM segments WHERE vod_id=?", (vod_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1


def test_events_survive_the_deletion_of_their_vod(conn):
    """Symetrique du precedent, pour la suppression de VOD.

    Le user supprime ses VOD pour liberer du disque (sources de plusieurs Go) :
    le rang du candidat exporte et le signal dominant jugent l'ALGORITHME, pas
    la VOD, et gardent tout leur sens apres sa disparition. Le corps exact de
    delete_vod est rejoue ici (clips puis vods).
    """
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    usage.record(conn, "clip_exported", vod_id=vod_id, rank=3, top_signal="chat")
    usage.record(conn, "bounds_adjusted", vod_id=vod_id, start_shift_s=-2.0, end_shift_s=0.0)
    conn.execute("DELETE FROM clips WHERE vod_id=?", (vod_id,))
    conn.execute("DELETE FROM vods WHERE id=?", (vod_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 2
