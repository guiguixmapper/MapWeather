"""
climbing.py
===========
Module de détection et catégorisation des ascensions pour l'app Vélo & Météo.

Fonctions publiques :
    - detecter_ascensions(df)        → liste des ascensions détectées
    - categoriser(distance_m, d_plus) → (catégorie, score)
    - estimer_watts(pente, vitesse, poids) → watts estimés en montée
    - estimer_fc(watts, ftp, fc_max)  → FC estimée
    - estimer_temps_col(dist_km, pente, vitesse) → (minutes, vitesse_montee)
    - calculer_calories(poids, duree, dist, d_plus, vitesse) → kcal
    - get_zone(valeur, ref, zones)    → (num, label, couleur)
    - zones_actives(mode)             → liste des zones selon le mode
    - COULEURS_CAT                    → dict catégorie → couleur hex
"""

import math
import pandas as pd

# ==============================================================================
# CATÉGORISATION (formule Strava)
# Score = (D+ en m × pente moyenne en %) / 100
# ==============================================================================

SEUILS_STRAVA = {
    "🔴 HC":          80,
    "🟠 1ère Cat.":   64,
    "🟡 2ème Cat.":   32,
    "🟢 3ème Cat.":   16,
    "🔵 4ème Cat.":    8,
    "⚪ Non classée":  2,
}

COULEURS_CAT = {
    "🔴 HC":          "#ef4444",
    "🟠 1ère Cat.":   "#f97316",
    "🟡 2ème Cat.":   "#eab308",
    "🟢 3ème Cat.":   "#22c55e",
    "🔵 4ème Cat.":   "#3b82f6",
    "⚪ Non classée": "#94a3b8",
}

LEGENDE_STRAVA = (
    "**Catégorisation Strava** — Score = (D+ × pente moy.) / 100 · "
    "⚪ Non classée ≥2 · 🔵 4ème ≥8 · 🟢 3ème ≥16 · "
    "🟡 2ème ≥32 · 🟠 1ère ≥64 · 🔴 HC ≥80"
)


def categoriser(distance_m: float, d_plus: float) -> tuple[str | None, float]:
    """
    Catégorise une ascension selon la formule Strava.
    Retourne (catégorie, score) ou (None, 0) si non qualifiable.
    """
    if distance_m < 300 or d_plus < 10:
        return None, 0
    pente_moy = (d_plus / distance_m) * 100
    if pente_moy < 2.0:
        return None, 0
    score = (d_plus * pente_moy) / 100
    for lbl, seuil in SEUILS_STRAVA.items():
        if score >= seuil:
            return lbl, round(score, 1)
    return None, 0


# ==============================================================================
# ZONES D'ENTRAÎNEMENT
# ==============================================================================

# Puissance (% FTP)
ZONES_PUISSANCE = [
    (0,    0.55, 1, "Z1 Récup",     "#94a3b8"),
    (0.55, 0.75, 2, "Z2 Endurance", "#3b82f6"),
    (0.75, 0.90, 3, "Z3 Tempo",     "#22c55e"),
    (0.90, 1.05, 4, "Z4 Seuil",     "#eab308"),
    (1.05, 1.20, 5, "Z5 VO2max",    "#f97316"),
    (1.20, 999,  6, "Z6 Anaérobie", "#ef4444"),
]

# Fréquence cardiaque (% FC max)
ZONES_FC = [
    (0,    0.60, 1, "Z1 Récup",     "#94a3b8"),
    (0.60, 0.70, 2, "Z2 Endurance", "#3b82f6"),
    (0.70, 0.80, 3, "Z3 Tempo",     "#22c55e"),
    (0.80, 0.90, 4, "Z4 Seuil",     "#eab308"),
    (0.90, 0.95, 5, "Z5 VO2max",    "#f97316"),
    (0.95, 999,  6, "Z6 Anaérobie", "#ef4444"),
]


def get_zone(valeur: float, ref: float, zones: list) -> tuple[int, str, str]:
    """Retourne (num_zone, label, couleur) selon le ratio valeur/ref."""
    ratio = valeur / ref if ref > 0 else 0
    for bas, haut, num, lbl, coul in zones:
        if bas <= ratio < haut:
            return num, lbl, coul
    return 6, "Z6 Anaérobie", "#ef4444"


def zones_actives(mode: str) -> list:
    """Retourne la liste de zones selon le mode (Puissance ou FC)."""
    return ZONES_PUISSANCE if mode == "⚡ Puissance" else ZONES_FC


# ==============================================================================
# ESTIMATION DE L'EFFORT
# ==============================================================================

def estimer_watts(pente_pct: float, vitesse_plat_kmh: float, poids_kg: float = 75) -> int:
    """
    Puissance estimée en montée à la vitesse réelle.
    La vitesse en montée est réduite selon la pente (10% de ralentissement par %).
    """
    g              = 9.81
    facteur        = 1 + (pente_pct * 0.10)
    vitesse_montee = max(5.0, vitesse_plat_kmh / facteur)
    vm             = vitesse_montee / 3.6
    pr             = math.atan(pente_pct / 100)
    return max(0, int(poids_kg * g * math.sin(pr) * vm + poids_kg * g * 0.004 * vm))


def estimer_fc(watts: int, ftp: float, fc_max: int, fc_repos: int = 50) -> int | None:
    """
    Estimation de la FC depuis les watts.
    Calage : FTP = 85% FC max. Résultat borné à fc_max - 3.
    """
    if ftp <= 0 or fc_max <= 0:
        return None
    watts_fc_max = ftp / 0.85
    ratio        = min(watts / watts_fc_max, 0.97)
    fc           = fc_repos + ratio * (fc_max - fc_repos)
    return int(min(fc_max - 3, max(fc_repos, fc)))


def estimer_temps_col(dist_km: float, pente_moy_pct: float,
                      vitesse_plat_kmh: float) -> tuple[int, float]:
    """
    Temps estimé pour gravir une montée (minutes) + vitesse de montée (km/h).
    10% de ralentissement par % de pente.
    """
    facteur        = 1 + (pente_moy_pct * 0.10)
    vitesse_montee = max(5.0, vitesse_plat_kmh / facteur)
    mins           = int((dist_km / vitesse_montee) * 60)
    return mins, round(vitesse_montee, 1)


def calculer_calories(poids_cycliste_kg: float, duree_sec: float,
                      dist_m: float, d_plus_m: float,
                      vitesse_kmh: float) -> int:
    """
    Estimation des calories brûlées via le MET (Metabolic Equivalent of Task).
    MET de base selon la vitesse, majoré par la pente moyenne globale.
    """
    if poids_cycliste_kg <= 0 or duree_sec <= 0:
        return 0
    duree_h        = duree_sec / 3600
    pente_globale  = (d_plus_m / dist_m * 100) if dist_m > 0 else 0

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

def _lisser(alts: list, f: int = 7) -> list:
    """Lissage par moyenne mobile — efface le bruit GPS sans déformer le profil."""
    demi, n, r = f // 2, len(alts), []
    for i in range(n):
        s, e = max(0, i - demi), min(n, i + demi + 1)
        r.append(sum(alts[s:e]) / (e - s))
    return r


def _pente_max(dists: list, alts: list, d0: int, s0: int) -> float:
    """Calcule la pente maximale sur une fenêtre glissante de 50m."""
    pm = 0.0
    for i in range(d0 + 1, s0 + 1):
        for j in range(i - 1, max(d0 - 1, i - 50), -1):
            dd = (dists[i] - dists[j]) * 1000
            if dd >= 50:
                p = ((alts[i] - alts[j]) / dd) * 100
                if 0 < p <= 40:
                    pm = max(pm, p)
                break
    return round(pm, 1)


def detecter_ascensions(df: pd.DataFrame) -> list[dict]:
    """
    Détecte et catégorise les ascensions dans un profil altimétrique.

    Algorithme :
    - Lissage léger du profil (f=7) pour effacer le bruit GPS
    - Une montée démarre quand on gagne > 5m depuis le dernier creux
    - Elle se termine quand on descend depuis le sommet de plus de :
        max(20m, min(100m, D+_courant × 15%))
      → seuil adaptatif : tolérant sur les grandes montées, strict sur les petites
    - Catégorisation Strava sur le segment détecté

    Args:
        df: DataFrame avec colonnes "Distance (km)" et "Altitude (m)"

    Returns:
        Liste de dicts avec les infos de chaque ascension, triée par position.
    """
    if df.empty or len(df) < 3:
        return []

    alts_raw = df["Altitude (m)"].tolist()
    dists    = df["Distance (km)"].tolist()
    alts     = _lisser(alts_raw)
    n        = len(alts)

    SEUIL_DEBUT = 5   # m de gain minimum pour démarrer une montée

    ascensions = []
    en_montee  = False
    creux_idx  = 0
    sommet_idx = 0

    def _enregistrer(debut_idx: int, som_idx: int) -> None:
        dk = dists[som_idx] - dists[debut_idx]
        dp = alts_raw[som_idx] - alts_raw[debut_idx]
        if dk <= 0 or dp <= 0:
            return
        cat, score = categoriser(dk * 1000, dp)
        if cat is None:
            return
        pm = (dp / (dk * 1000)) * 100
        ascensions.append({
            "Catégorie":   cat,
            "Départ (km)": round(dists[debut_idx], 1),
            "Sommet (km)": round(dists[som_idx], 1),
            "Longueur":    f"{round(dk, 1)} km",
            "Dénivelé":    f"{int(dp)} m",
            "Pente moy.":  f"{round(pm, 1)} %",
            "Pente max":   f"{_pente_max(dists, alts_raw, debut_idx, som_idx)} %",
            "Alt. sommet": f"{int(alts_raw[som_idx])} m",
            "Score":       score,
            # Clés internes (préfixe _ = usage interne uniquement)
            "_debut_km":   dists[debut_idx],
            "_sommet_km":  dists[som_idx],
            "_pente_moy":  pm,
        })

    for i in range(1, n):
        a = alts[i]
        if not en_montee:
            if a < alts[creux_idx]:
                creux_idx = i
            elif a >= alts[creux_idx] + SEUIL_DEBUT:
                en_montee  = True
                sommet_idx = i
        else:
            if a > alts[sommet_idx]:
                sommet_idx = i
            else:
                # Seuil adaptatif : 15% du D+ courant, entre 20m et 100m
                d_plus_courant = alts[sommet_idx] - alts[creux_idx]
                seuil_fin      = max(20, min(100, d_plus_courant * 0.15))
                if a <= alts[sommet_idx] - seuil_fin:
                    _enregistrer(creux_idx, sommet_idx)
                    en_montee  = False
                    creux_idx  = i
                    sommet_idx = i

    # Montée encore en cours en fin de parcours (ex : étape qui finit au sommet)
    if en_montee:
        _enregistrer(creux_idx, sommet_idx)

    ascensions.sort(key=lambda x: x["_debut_km"])
    return ascensions
