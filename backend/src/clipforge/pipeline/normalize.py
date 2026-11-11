def rank_normalize(values: list[float]) -> list[float]:
    """Normalise par rang : la plus grande valeur vaut 1.0, la plus petite 0.0.

    Les valeurs nulles (silence, aucun message) restent a 0.0 et ne participent
    pas au classement : une VOD a moitie muette ne doit pas voir son silence
    classe « moyen ». Les ex aequo partagent le meme rang.
    """
    if not values:
        return []
    positives = sorted({v for v in values if v > 0.0})
    if not positives:
        return [0.0] * len(values)
    if len(positives) == 1:
        return [1.0 if v > 0.0 else 0.0 for v in values]
    rank_of = {v: i / (len(positives) - 1) for i, v in enumerate(positives)}
    return [rank_of[v] if v > 0.0 else 0.0 for v in values]
