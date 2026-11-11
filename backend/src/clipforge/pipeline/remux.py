"""Le remux, vu de l'exterieur : diagnostiquer un conteneur, puis le reparer.

Facade stable. Le module a ete coupe en deux quand il a passe la barre des 300
lignes, le long de ses deux responsabilites reelles :

- remux_detection.py — LIRE le conteneur et nommer le defaut, sans rien ecrire ;
- remux_repair.py — reecrire le fichier et refuser un remplacant douteux.

Les appelants (steps, runner, download, health, diagnostic, migrate_seekable)
continuent d'importer `clipforge.pipeline.remux` : c'est le nom que porte cette
etape du pipeline, et il ne bouge pas parce que son implementation a grossi. La
troisieme etape que l'on cherche parfois ici — ECARTER une source morte — n'est
pas dans ce module et n'a pas a y venir : elle vit dans download.mettre_de_cote,
qui explique pourquoi (liberer le nom pour un telechargement neuf n'est pas une
reparation de conteneur).
"""
from clipforge.pipeline.remux_detection import (
    TS_PACKET,
    TS_SYNC,
    PlanDeRemux,
    besoin_de_remux,
    is_fragmented,
    is_mpegts,
    plan_de_remux,
    remux_prevu,
)
from clipforge.pipeline.remux_repair import (
    RemuxError,
    SourceInexploitableError,
    ensure_seekable,
)

__all__ = [
    "TS_PACKET",
    "TS_SYNC",
    "PlanDeRemux",
    "RemuxError",
    "SourceInexploitableError",
    "besoin_de_remux",
    "ensure_seekable",
    "is_fragmented",
    "is_mpegts",
    "plan_de_remux",
    "remux_prevu",
]
