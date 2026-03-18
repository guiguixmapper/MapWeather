"""
climbing.py
===========
Détection et catégorisation des ascensions — formule UCI officielle.

Algorithme de détection du point de départ :
    Approche par point d'inflexion de pente — on remonte depuis le sommet
    et on cherche le dernier endroit où la pente moyenne (sur 2km) passe
    sous 1% de manière soutenue. C'est "là où ça commence vraiment à monter".

Pipeline complet :
    1. Lissage léger (f=7) pour effacer le bruit GPS
    2. Détection des sommets locaux significatifs
    3. Pour chaque sommet, point de départ = inflexion de pente
    4. Fusion des ascensions consécutives si descente inter < 15% du D+ combiné
    5. Filtrage D+ >= 50m
    6. Catégorisation UCI : Score = (D+ x pente_moy) / 100
"""

import math
import pandas as pd

# ==============================================================================
# CATÉGORISATION UCI
# ==============================================================================

SEUILS_UCI = {
    "🔴 HC":          80,
    "🟠 1ère Cat.":   40,
    "🟡 2ème Cat.":   20,
    "🟢 3ème Cat.":    8,
    "🔵 4ème Cat.":    2,
    "⚪ NC":           0,   # Non classée — petite côte sous les seuils UCI
}

COULEURS_CAT = {
    "🔴 HC":          "#ef4444",
    "🟠 1ère Cat.":   "#f97316",
    "🟡 2ème Cat.":   "#eab308",
    "🟢 3ème Cat.":   "#22c55e",
    "🔵 4ème Cat.":   "#3b82f6",
    "⚪ NC":          "#94a3b8",
}

LEGENDE_UCI = (
    "**Catégorisation UCI** — Score = (D+ x pente moy.) / 100 · "
    "⚪ NC ≥0 · 🔵 4ème ≥2 · 🟢 3ème ≥8 · 🟡 2ème ≥20 · 🟠 1ère ≥40 · 🔴 HC ≥80"
)

D_PLUS_MIN   = 30     # m — abaissé pour capter les petites côtes
SEUIL_PENTE  = 1.0    # % — seuil d'inflexion de pente
FENETRE_KM   = 2.0    # km — fenêtre de calcul de la pente


def categoriser_uci(distance_m, d_plus):
    """
    Catégorisation UCI : Score = (D+ x pente_moy) / 100.
    Retourne (catégorie, score) ou (None, 0) si non qualifiable.
    Les montées avec D+ >= 30m mais score < 2 sont classées ⚪ NC.
    """
    if distance_m < 200 or d_plus < D_PLUS_MIN:
        return None, 0.0
    pente_moy = (d_plus / distance_m) * 100
    if pente_moy < 1.5:   # pente trop faible → faux-plat ignoré
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
    ratio = valeur / ref if ref > 0 else 0
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
    """FC estimée depuis les watts. Calage FTP = 85% FC max."""
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

def _lisser(alts, f=7):
    """Lissage par moyenne mobile symétrique."""
    demi, n, r = f // 2, len(alts), []
    for i in range(n):
        s, e = max(0, i - demi), min(n, i + demi + 1)
        r.append(sum(alts[s:e]) / (e - s))
    return r


def _pente_sur_fenetre(dists, alts, idx, fenetre_km):
    """Pente moyenne (%) calculée sur une fenêtre de fenetre_km avant idx."""
    for j in range(idx - 1, -1, -1):
        dist_m = (dists[idx] - dists[j]) * 1000
        if dist_m >= fenetre_km * 1000:
            return (alts[idx] - alts[j]) / dist_m * 100
    return 0.0


def _trouver_depart_inflexion(dists, alts, borne_gauche, sommet_idx):
    """
    Trouve le point de départ de la montée par inflexion de pente.

    On remonte depuis le sommet et on cherche le dernier point où la pente
    (calculée sur FENETRE_KM en arrière) passe sous SEUIL_PENTE.

    Si on ne trouve pas d'inflexion claire, on retourne le creux absolu
    dans la fenêtre de recherche.

    Args:
        borne_gauche : index de début de la zone de recherche
        sommet_idx   : index du sommet

    Returns:
        Index du point de départ.
    """
    # Calcule la pente glissante sur tout le segment
    pentes = [
        (i, _pente_sur_fenetre(dists, alts, i, FENETRE_KM))
        for i in range(borne_gauche, sommet_idx + 1)
    ]

    if not pentes:
        return borne_gauche

    # Remonte depuis le sommet — cherche le dernier passage sous SEUIL_PENTE
    for k in range(len(pentes) - 1, 0, -1):
        idx, p = pentes[k]
        if p < SEUIL_PENTE:
            return idx

    # Pas d'inflexion trouvée → creux absolu (fallback)
    return min(range(borne_gauche, sommet_idx), key=lambda i: alts[i])


def _detecter_sommets(dists, alts_lisses):
    """
    Trouve les sommets locaux significatifs du profil.
    Un sommet est retenu si on descend ensuite de plus de MARGE depuis lui.
    MARGE adaptive : 12% du D+ depuis le dernier creux, min 15m, max 200m.
    """
    n         = len(alts_lisses)
    sommets   = []
    en_montee = False
    creux_idx = 0
    som_idx   = 0

    # Si le parcours commence en montée dès le départ,
    # on initialise directement en mode montée
    for i in range(1, min(20, n)):
        if alts_lisses[i] > alts_lisses[0] + 10:
            en_montee = True
            som_idx   = i
            break

    for i in range(1, n):
        a = alts_lisses[i]
        if not en_montee:
            if a < alts_lisses[creux_idx]:
                creux_idx = i
            elif a >= alts_lisses[creux_idx] + 10:
                en_montee = True
                som_idx   = i
        else:
            if a > alts_lisses[som_idx]:
                som_idx = i
            else:
                d_plus_c = alts_lisses[som_idx] - alts_lisses[creux_idx]
                marge    = max(15.0, min(200.0, d_plus_c * 0.12))
                if a <= alts_lisses[som_idx] - marge:
                    sommets.append((creux_idx, som_idx))
                    en_montee = False
                    creux_idx = i
                    som_idx   = i

    # Montée en cours à la fin du parcours
    if en_montee and som_idx > creux_idx:
        sommets.append((creux_idx, som_idx))

    return sommets


def _fusionner(sommets, alts_lisses):
    """
    Fusionne deux segments consécutifs uniquement si la descente intermédiaire
    est faible EN VALEUR ABSOLUE (< 50m) ET en proportion (< 20% du D+ du 2ème segment).
    
    Logique : un replat sur une montée descend peu en absolu.
    Une vraie descente entre deux cols descend beaucoup.
    """
    if not sommets:
        return []

    fusionnes = [list(sommets[0])]

    for creux, sommet in sommets[1:]:
        prev_creux, prev_sommet = fusionnes[-1]

        descente_abs  = alts_lisses[prev_sommet] - alts_lisses[creux]
        d_plus_second = alts_lisses[sommet] - alts_lisses[creux]

        # Fusion seulement si :
        # - descente absolue < 60m (c'est un replat, pas une vraie descente)
        # - ET descente < 25% du D+ du second segment
        if descente_abs > 0 and descente_abs < 60 and descente_abs < d_plus_second * 0.25:
            nouveau_som = (
                sommet if alts_lisses[sommet] >= alts_lisses[prev_sommet]
                else prev_sommet
            )
            fusionnes[-1] = [prev_creux, nouveau_som]
        else:
            fusionnes.append([creux, sommet])

    return [tuple(s) for s in fusionnes]


def _pente_max(dists, alts, i0, i1, fenetre_m=100.0):
    """Pente maximale sur une fenêtre glissante de fenetre_m mètres."""
    pm = 0.0
    for i in range(i0 + 1, i1 + 1):
        for j in range(i - 1, max(i0 - 1, i - 200), -1):
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
        1. Lissage
        2. Détection des sommets locaux
        3. Fusion des ascensions avec replat intermédiaire
        4. Point de départ par inflexion de pente (remonte depuis le sommet)
        5. Filtrage D+ >= 50m + catégorisation UCI

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
    alts_lisses = _lisser(alts_raw)

    # Étape 1 : sommets + fusion
    sommets  = _detecter_sommets(dists, alts_lisses)
    sommets  = _fusionner(sommets, alts_lisses)

    ascensions = []
    for k, (creux_idx, sommet_idx) in enumerate(sommets):
        borne_gauche = sommets[k-1][1] if k > 0 else 0

        # Longueur approximative depuis le creux détecté
        dk_brut = dists[sommet_idx] - dists[creux_idx]

        if dk_brut >= 20.0:
            # Très grande montée (>= 20km) → creux absolu depuis la borne gauche
            # L'inflexion est trop instable sur de longues montées progressives
            debut_idx = min(
                range(borne_gauche, sommet_idx),
                key=lambda i: alts_lisses[i]
            )
        elif dk_brut >= 5.0:
            # Grande montée (5-20km) → inflexion de pente
            debut_idx = _trouver_depart_inflexion(
                dists, alts_lisses, borne_gauche, sommet_idx
            )
        else:
            # Petite montée (< 5km) → creux absolu
            debut_idx = min(
                range(borne_gauche, sommet_idx),
                key=lambda i: alts_lisses[i]
            )

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
