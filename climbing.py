"""
climbing.py — v6
================
Détection et catégorisation des ascensions — algorithme "slope-first".

Principe :
    On scanne le profil de gauche à droite en calculant une pente glissante
    sur une fenêtre de distance réelle. Une montée démarre quand la pente
    dépasse SEUIL_DEBUT et reste haute ; elle se termine quand elle repasse
    sous SEUIL_FIN de manière soutenue.

Pipeline complet :
    1. Lissage léger (fenêtre 5 points) pour effacer le bruit GPS
    2. Calcul de la pente glissante sur FENETRE_PENTE_M mètres réels
    3. Détection des "runs" de pente : zones où pente > SEUIL_DEBUT
    4. Fusion des runs séparés par une descente < MAX_DESCENTE_FUSION_M
    5. Filtrage : D+ >= D_PLUS_MIN et longueur >= DISTANCE_MIN_M
    6. Catégorisation UCI : Score = (D+ × pente_moy) / 100

Avantages vs v5 (sommet-first) :
    - Le départ = là où ça monte vraiment, par définition du seuil
    - Pas d'heuristique différente selon la taille de la montée
    - Les montées progressives sont détectées naturellement
    - Comportement prévisible et transparent sur tous types de parcours
"""

import math
import pandas as pd

# ==============================================================================
# PARAMÈTRES — DÉTECTION
# ==============================================================================

LISSAGE_F           = 5      # points — fenêtre de lissage (impair)
FENETRE_PENTE_M     = 300    # m — fenêtre de calcul de la pente glissante
SEUIL_DEBUT         = 2.0    # % — seuil pour démarrer une montée
SEUIL_FIN           = 1.0    # % — seuil pour terminer une montée
MIN_RUN_M           = 300    # m — longueur minimale d'un run pour être retenu
MAX_DESCENTE_FUSION_M = 50   # m de D− max pour fusionner deux runs consécutifs

# ==============================================================================
# PARAMÈTRES — FILTRAGE & CATÉGORISATION
# ==============================================================================

D_PLUS_MIN    = 30    # m — dénivelé minimum pour retenir une montée
DISTANCE_MIN_M = 500  # m — longueur minimale pour retenir une montée
PENTE_MIN_CAT = 1.0   # % — pente moyenne minimale pour catégoriser

SEUILS_UCI = {
    # Ordre décroissant garanti (Python 3.7+, insertion order)
    "🔴 HC":          80,
    "🟠 1ère Cat.":   40,
    "🟡 2ème Cat.":   20,
    "🟢 3ème Cat.":    8,
    "🔵 4ème Cat.":    2,
    "⚪ NC":           0,
}

COULEURS_CAT = {
    "🔴 HC":         "#ef4444",
    "🟠 1ère Cat.":  "#f97316",
    "🟡 2ème Cat.":  "#eab308",
    "🟢 3ème Cat.":  "#22c55e",
    "🔵 4ème Cat.":  "#3b82f6",
    "⚪ NC":         "#94a3b8",
}

LEGENDE_UCI = (
    "**Catégorisation UCI** — Score = (D+ × pente moy.) / 100 · "
    "⚪ NC ≥0 · 🔵 4ème ≥2 · 🟢 3ème ≥8 · 🟡 2ème ≥20 · 🟠 1ère ≥40 · 🔴 HC ≥80"
)


# ==============================================================================
# CATÉGORISATION UCI
# ==============================================================================

def categoriser_uci(distance_m, d_plus):
    """
    Catégorisation UCI : Score = (D+ × pente_moy) / 100.

    Retourne (catégorie, score) ou (None, 0.0) si non qualifiable.
    """
    if distance_m < DISTANCE_MIN_M or d_plus < D_PLUS_MIN:
        return None, 0.0
    pente_moy = (d_plus / distance_m) * 100
    if pente_moy < PENTE_MIN_CAT:
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
    """Retourne (num_zone, label, couleur) selon le ratio valeur/ref."""
    if ref <= 0:
        return 1, "Z1 Récup", "#94a3b8"
    ratio = valeur / ref
    for bas, haut, num, lbl, coul in zones:
        if bas <= ratio < haut:
            return num, lbl, coul
    return 6, "Z6 Anaérobie", "#ef4444"


def zones_actives(mode):
    """Retourne la liste de zones selon le mode."""
    return ZONES_PUISSANCE if mode == "⚡ Puissance" else ZONES_FC


# ==============================================================================
# ESTIMATION DE L'EFFORT
# ==============================================================================

def estimer_watts(pente_pct, vitesse_plat_kmh, poids_kg=75):
    """Puissance estimée en montée à la vitesse réelle."""
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
    """
    FC estimée depuis les watts.
    Hypothèse : FTP ≈ 85% FC max.
    """
    if ftp <= 0 or fc_max <= 0:
        return None
    ratio = min(watts / (ftp / 0.85), 0.97)
    fc    = fc_repos + ratio * (fc_max - fc_repos)
    return int(min(fc_max - 3, max(fc_repos, fc)))


def estimer_temps_col(dist_km, pente_moy_pct, vitesse_plat_kmh):
    """Temps estimé (min) et vitesse de montée (km/h)."""
    facteur        = 1.0 + pente_moy_pct * 0.10
    vitesse_montee = max(5.0, vitesse_plat_kmh / facteur)
    return int((dist_km / vitesse_montee) * 60), round(vitesse_montee, 1)


def calculer_calories(poids_cycliste_kg, duree_sec, dist_m, d_plus_m, vitesse_kmh):
    """Calories via MET adapté au cyclisme."""
    if poids_cycliste_kg <= 0 or duree_sec <= 0:
        return 0
    duree_h       = duree_sec / 3600
    pente_globale = (d_plus_m / dist_m * 100) if dist_m > 0 else 0
    if vitesse_kmh < 16:   met = 6.0
    elif vitesse_kmh < 20: met = 8.0
    elif vitesse_kmh < 25: met = 10.0
    elif vitesse_kmh < 30: met = 12.0
    else:                  met = 14.0
    return int(min(met + pente_globale * 0.8, 18.0) * poids_cycliste_kg * duree_h)


# ==============================================================================
# DÉTECTION — FONCTIONS INTERNES
# ==============================================================================

def _lisser(alts, f=LISSAGE_F):
    """Lissage par moyenne mobile symétrique."""
    demi, n, r = f // 2, len(alts), []
    for i in range(n):
        s, e = max(0, i - demi), min(n, i + demi + 1)
        r.append(sum(alts[s:e]) / (e - s))
    return r


def _calc_pentes(dists, alts, fenetre_m=FENETRE_PENTE_M):
    """
    Calcule la pente glissante (%) en chaque point sur une fenêtre
    de fenetre_m mètres réels en arrière.

    Retourne une liste de floats (même longueur que dists).
    Les premiers points sans fenêtre complète reçoivent la pente locale.
    """
    n      = len(dists)
    pentes = [0.0] * n
    for i in range(1, n):
        for j in range(i - 1, -1, -1):
            dist_m = (dists[i] - dists[j]) * 1000
            if dist_m >= fenetre_m:
                pentes[i] = (alts[i] - alts[j]) / dist_m * 100
                break
            if j == 0:
                # Fenêtre incomplète : pente locale depuis le début
                dist_m = (dists[i] - dists[0]) * 1000
                if dist_m > 0:
                    pentes[i] = (alts[i] - alts[0]) / dist_m * 100
    return pentes


def _detecter_runs(dists, alts, pentes):
    """
    Détecte les "runs" de pente : intervalles [i_debut, i_fin] où la pente
    est au-dessus de SEUIL_DEBUT de manière soutenue (>= MIN_RUN_M).

    Retourne une liste de tuples (idx_debut, idx_fin).
    """
    n     = len(dists)
    runs  = []
    debut = None

    for i in range(n):
        if pentes[i] >= SEUIL_DEBUT:
            if debut is None:
                debut = i
        else:
            if debut is not None:
                # Vérifier que le run est assez long
                dist_run = (dists[i - 1] - dists[debut]) * 1000
                if dist_run >= MIN_RUN_M:
                    runs.append((debut, i - 1))
                debut = None

    # Run en cours à la fin du profil
    if debut is not None:
        dist_run = (dists[-1] - dists[debut]) * 1000
        if dist_run >= MIN_RUN_M:
            runs.append((debut, n - 1))

    return runs


def _fusionner_runs(runs, dists, alts):
    """
    Fusionne deux runs consécutifs si la descente entre eux
    est < MAX_DESCENTE_FUSION_M en dénivelé négatif absolu.

    "Descente" = différence d'altitude entre la fin du 1er run
    et le point le plus bas avant le début du 2ème run.
    """
    if not runs:
        return []

    fusionnes = [list(runs[0])]

    for debut, fin in runs[1:]:
        prev_debut, prev_fin = fusionnes[-1]

        # Point le plus bas dans l'intervalle entre les deux runs
        alt_vallee = min(alts[prev_fin:debut + 1])
        descente   = alts[prev_fin] - alt_vallee

        if descente < MAX_DESCENTE_FUSION_M:
            # Fusion : on étend le run précédent jusqu'à la fin du run courant
            fusionnes[-1][1] = fin
        else:
            fusionnes.append([debut, fin])

    return [tuple(r) for r in fusionnes]


def _pente_max(dists, alts, i0, i1, fenetre_m=100.0):
    """Pente maximale sur une fenêtre glissante de fenetre_m mètres réels."""
    pm = 0.0
    for i in range(i0 + 1, i1 + 1):
        for j in range(i - 1, i0 - 1, -1):
            dist_m = (dists[i] - dists[j]) * 1000
            if dist_m >= fenetre_m:
                p = ((alts[i] - alts[j]) / dist_m) * 100
                if 0 < p <= 40:
                    pm = max(pm, p)
                break
    return round(pm, 1)


# ==============================================================================
# DÉTECTION — FONCTION PRINCIPALE
# ==============================================================================

def detecter_ascensions(df):
    """
    Détecte et catégorise les ascensions dans un profil altimétrique.

    Pipeline :
        1. Lissage (moyenne mobile LISSAGE_F points)
        2. Pente glissante sur FENETRE_PENTE_M mètres réels
        3. Détection des runs de pente > SEUIL_DEBUT
        4. Fusion des runs séparés par < MAX_DESCENTE_FUSION_M de descente
        5. Filtrage D+ >= D_PLUS_MIN et distance >= DISTANCE_MIN_M
        6. Catégorisation UCI

    Args:
        df : DataFrame avec colonnes "Distance (km)" et "Altitude (m)".

    Returns:
        Liste de dicts triée par position sur le parcours.
        Clés internes préfixées par _.
    """
    if df.empty or len(df) < 5:
        return []

    alts_raw    = df["Altitude (m)"].tolist()
    dists       = df["Distance (km)"].tolist()
    alts        = _lisser(alts_raw)       # travail sur profil lissé
    pentes      = _calc_pentes(dists, alts)

    runs        = _detecter_runs(dists, alts, pentes)
    runs        = _fusionner_runs(runs, dists, alts)

    ascensions  = []
    for (i0, i1) in runs:
        dk = dists[i1] - dists[i0]         # km
        dp = alts[i1] - alts[i0]           # m, sur profil lissé

        if dk <= 0 or dp < D_PLUS_MIN:
            continue

        cat, score = categoriser_uci(dk * 1000, dp)
        if cat is None:
            continue

        pm = (dp / (dk * 1000)) * 100

        ascensions.append({
            # ── Affichage ──────────────────────────────────────────────
            "Catégorie":   cat,
            "Départ (km)": round(dists[i0], 1),
            "Sommet (km)": round(dists[i1], 1),
            "Longueur":    f"{round(dk, 1)} km",
            "Dénivelé":    f"{int(dp)} m",
            "Pente moy.":  f"{round(pm, 1)} %",
            "Pente max":   f"{_pente_max(dists, alts_raw, i0, i1)} %",
            "Alt. sommet": f"{int(alts_raw[i1])} m",
            "Score UCI":   score,
            # ── Clés internes (utilisées par app.py) ───────────────────
            "_debut_km":   dists[i0],
            "_sommet_km":  dists[i1],
            "_pente_moy":  pm,
        })

    ascensions.sort(key=lambda x: x["_debut_km"])
    return ascensions
