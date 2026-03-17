"""
climbing.py
===========
Détection et catégorisation des ascensions — formule UCI officielle.

Algorithme :
    1. Lissage léger du profil GPS (moyenne mobile f=7)
    2. Détection des segments montants bruts
    3. Fusion des segments séparés par une descente < 15% du D+ combiné
    4. Filtrage : D+ >= 50m
    5. Catégorisation UCI : Score = (D+ x pente_moy) / 100

Fonctions publiques :
    detecter_ascensions(df)
    categoriser_uci(distance_m, d_plus)
    estimer_watts(pente, vitesse, poids)
    estimer_fc(watts, ftp, fc_max)
    estimer_temps_col(dist_km, pente, vitesse)
    calculer_calories(poids, duree, dist, d_plus, vitesse)
    get_zone(valeur, ref, zones)
    zones_actives(mode)
    COULEURS_CAT, LEGENDE_UCI
"""

import math
import pandas as pd

# ==============================================================================
# CATÉGORISATION UCI OFFICIELLE
# ==============================================================================

SEUILS_UCI = {
    "🔴 HC":        80,
    "🟠 1ère Cat.": 40,
    "🟡 2ème Cat.": 20,
    "🟢 3ème Cat.":  8,
    "🔵 4ème Cat.":  2,
}

COULEURS_CAT = {
    "🔴 HC":          "#ef4444",
    "🟠 1ère Cat.":   "#f97316",
    "🟡 2ème Cat.":   "#eab308",
    "🟢 3ème Cat.":   "#22c55e",
    "🔵 4ème Cat.":   "#3b82f6",
}

LEGENDE_UCI = (
    "**Catégorisation UCI** — Score = (D+ x pente moy.) / 100 · "
    "🔵 4ème >=2 · 🟢 3ème >=8 · 🟡 2ème >=20 · 🟠 1ère >=40 · 🔴 HC >=80"
)

D_PLUS_MIN = 50


def categoriser_uci(distance_m, d_plus):
    if distance_m < 500 or d_plus < D_PLUS_MIN:
        return None, 0.0
    pente_moy = (d_plus / distance_m) * 100
    if pente_moy < 2.0:
        return None, 0.0
    score = (d_plus * pente_moy) / 100
    for label, seuil in SEUILS_UCI.items():
        if score >= seuil:
            return label, round(score, 1)
    return None, 0.0


# ==============================================================================
# ZONES D'ENTRAÎNEMENT
# ==============================================================================

ZONES_PUISSANCE = [
    (0.00, 0.55, 1, "Z1 Récup",     "#94a3b8"),
    (0.55, 0.75, 2, "Z2 Endurance", "#3b82f6"),
    (0.75, 0.90, 3, "Z3 Tempo",     "#22c55e"),
    (0.90, 1.05, 4, "Z4 Seuil",     "#eab308"),
    (1.05, 1.20, 5, "Z5 VO2max",    "#f97316"),
    (1.20, 999., 6, "Z6 Anaérobie", "#ef4444"),
]

ZONES_FC = [
    (0.00, 0.60, 1, "Z1 Récup",     "#94a3b8"),
    (0.60, 0.70, 2, "Z2 Endurance", "#3b82f6"),
    (0.70, 0.80, 3, "Z3 Tempo",     "#22c55e"),
    (0.80, 0.90, 4, "Z4 Seuil",     "#eab308"),
    (0.90, 0.95, 5, "Z5 VO2max",    "#f97316"),
    (0.95, 999., 6, "Z6 Anaérobie", "#ef4444"),
]


def get_zone(valeur, ref, zones):
    ratio = valeur / ref if ref > 0 else 0
    for bas, haut, num, lbl, coul in zones:
        if bas <= ratio < haut:
            return num, lbl, coul
    return 6, "Z6 Anaérobie", "#ef4444"


def zones_actives(mode):
    return ZONES_PUISSANCE if mode == "⚡ Puissance" else ZONES_FC


# ==============================================================================
# ESTIMATION DE L'EFFORT
# ==============================================================================

def estimer_watts(pente_pct, vitesse_plat_kmh, poids_kg=75):
    g              = 9.81
    facteur        = 1.0 + pente_pct * 0.10
    vitesse_montee = max(5.0, vitesse_plat_kmh / facteur)
    vm             = vitesse_montee / 3.6
    angle          = math.atan(pente_pct / 100)
    return max(0, int(
        poids_kg * g * math.sin(angle) * vm +
        poids_kg * g * 0.004 * vm
    ))


def estimer_fc(watts, ftp, fc_max, fc_repos=50):
    if ftp <= 0 or fc_max <= 0:
        return None
    watts_100pct_fc = ftp / 0.85
    ratio           = min(watts / watts_100pct_fc, 0.97)
    fc              = fc_repos + ratio * (fc_max - fc_repos)
    return int(min(fc_max - 3, max(fc_repos, fc)))


def estimer_temps_col(dist_km, pente_moy_pct, vitesse_plat_kmh):
    facteur        = 1.0 + pente_moy_pct * 0.10
    vitesse_montee = max(5.0, vitesse_plat_kmh / facteur)
    mins           = int((dist_km / vitesse_montee) * 60)
    return mins, round(vitesse_montee, 1)


def calculer_calories(poids_cycliste_kg, duree_sec, dist_m, d_plus_m, vitesse_kmh):
    if poids_cycliste_kg <= 0 or duree_sec <= 0:
        return 0
    duree_h       = duree_sec / 3600
    pente_globale = (d_plus_m / dist_m * 100) if dist_m > 0 else 0
    if vitesse_kmh < 16:   met = 6.0
    elif vitesse_kmh < 20: met = 8.0
    elif vitesse_kmh < 25: met = 10.0
    elif vitesse_kmh < 30: met = 12.0
    else:                  met = 14.0
    met = min(met + pente_globale * 0.8, 18.0)
    return int(met * poids_cycliste_kg * duree_h)


# ==============================================================================
# DÉTECTION DES ASCENSIONS
# ==============================================================================

def _lisser(alts, f=7):
    """Lissage par moyenne mobile symétrique."""
    demi, n, r = f // 2, len(alts), []
    for i in range(n):
        s, e = max(0, i - demi), min(n, i + demi + 1)
        r.append(sum(alts[s:e]) / (e - s))
    return r


def _pente_max(dists, alts, i0, i1, fenetre_m=100.0):
    """Pente max sur une fenêtre glissante de fenetre_m mètres."""
    pm = 0.0
    for i in range(i0 + 1, i1 + 1):
        for j in range(i - 1, max(i0 - 1, i - 200), -1):
            dist_diff_m = (dists[i] - dists[j]) * 1000
            if dist_diff_m >= fenetre_m:
                pente = ((alts[i] - alts[j]) / dist_diff_m) * 100
                if 0 < pente <= 40:
                    pm = max(pm, pente)
                break
    return round(pm, 1)


def _construire_segments(dists, alts_lisses):
    """
    Détecte les segments montants bruts.
    Démarre quand gain > 8m depuis le dernier creux.
    Se termine quand descente > max(15m, min(60m, D+*20%)) depuis le sommet.
    """
    SEUIL_DEBUT = 8
    segments    = []
    en_montee   = False
    creux_idx   = 0
    sommet_idx  = 0

    for i in range(1, len(alts_lisses)):
        a = alts_lisses[i]
        if not en_montee:
            if a < alts_lisses[creux_idx]:
                creux_idx = i
            elif a >= alts_lisses[creux_idx] + SEUIL_DEBUT:
                en_montee  = True
                sommet_idx = i
        else:
            if a > alts_lisses[sommet_idx]:
                sommet_idx = i
            else:
                d_plus_c  = alts_lisses[sommet_idx] - alts_lisses[creux_idx]
                seuil_fin = max(15.0, min(60.0, d_plus_c * 0.20))
                if a <= alts_lisses[sommet_idx] - seuil_fin:
                    segments.append((creux_idx, sommet_idx))
                    en_montee  = False
                    creux_idx  = i
                    sommet_idx = i

    if en_montee and sommet_idx > creux_idx:
        segments.append((creux_idx, sommet_idx))

    return segments


def _fusionner_segments(segments, alts_lisses):
    """
    Fusionne les segments consécutifs dont la descente intermédiaire
    est inférieure à 15% du D+ combiné — gère les cols avec replat.
    """
    SEUIL_FUSION = 0.15

    if not segments:
        return []

    fusionnes = [list(segments[0])]

    for debut, sommet in segments[1:]:
        prev_debut, prev_sommet = fusionnes[-1]

        descente_inter = alts_lisses[prev_sommet] - alts_lisses[debut]
        d_plus_combine = (
            (alts_lisses[prev_sommet] - alts_lisses[prev_debut]) +
            (alts_lisses[sommet]      - alts_lisses[debut])
        )

        if 0 < descente_inter < d_plus_combine * SEUIL_FUSION:
            nouveau_sommet = (
                sommet if alts_lisses[sommet] >= alts_lisses[prev_sommet]
                else prev_sommet
            )
            fusionnes[-1] = [prev_debut, nouveau_sommet]
        else:
            fusionnes.append([debut, sommet])

    return [tuple(s) for s in fusionnes]


def detecter_ascensions(df):
    """
    Détecte et catégorise les ascensions dans un profil altimétrique.

    Pipeline : lissage -> segments bruts -> fusion -> filtrage -> UCI

    Args:
        df: DataFrame avec colonnes "Distance (km)" et "Altitude (m)".

    Returns:
        Liste de dicts triée par position. Clés internes préfixées par _.
    """
    if df.empty or len(df) < 5:
        return []

    alts_raw    = df["Altitude (m)"].tolist()
    dists       = df["Distance (km)"].tolist()
    alts_lisses = _lisser(alts_raw)

    segments = _construire_segments(dists, alts_lisses)
    segments = _fusionner_segments(segments, alts_lisses)

    ascensions = []
    for debut_idx, sommet_idx in segments:
        dk = dists[sommet_idx] - dists[debut_idx]
        dp = alts_raw[sommet_idx] - alts_raw[debut_idx]

        if dk <= 0 or dp < D_PLUS_MIN:
            continue

        cat, score = categoriser_uci(dk * 1000, dp)
        if cat is None:
            continue

        pm = (dp / (dk * 1000)) * 100

        ascensions.append({
            "Catégorie":   cat,
            "Départ (km)": round(dists[debut_idx], 1),
            "Sommet (km)": round(dists[sommet_idx], 1),
            "Longueur":    f"{round(dk, 1)} km",
            "Dénivelé":    f"{int(dp)} m",
            "Pente moy.":  f"{round(pm, 1)} %",
            "Pente max":   f"{_pente_max(dists, alts_raw, debut_idx, sommet_idx)} %",
            "Alt. sommet": f"{int(alts_raw[sommet_idx])} m",
            "Score UCI":   score,
            "_debut_km":   dists[debut_idx],
            "_sommet_km":  dists[sommet_idx],
            "_pente_moy":  pm,
        })

    ascensions.sort(key=lambda x: x["_debut_km"])
    return ascensions
