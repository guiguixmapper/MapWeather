"""
🚴‍♂️ Vélo & Météo — v2
Améliorations : détection côtes robuste, catégorisation inspirée UCI loisir,
carte grande en premier, profil Plotly interactif, surbrillance des ascensions,
graphique vent interactif, légendes sur tous les tableaux.
"""

import streamlit as st
import pandas as pd
import gpxpy
import folium
from streamlit_folium import st_folium
import requests
from datetime import datetime, timedelta, date
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import math
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# SECTION 1 : FONCTIONS UTILITAIRES
# ==============================================================================

def calculer_cap(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def direction_vent_relative(cap_velo, dir_vent):
    diff = (dir_vent - cap_velo) % 360
    if diff <= 45 or diff >= 315:   return "⬇️ Face"
    elif 135 <= diff <= 225:        return "⬆️ Dos"
    elif 45 < diff < 135:           return "↘️ Côté (D)"
    else:                           return "↙️ Côté (G)"


def obtenir_icone_meteo(code):
    mapping = {
        0: "☀️ Clair", 1: "⛅ Éclaircies", 2: "⛅ Éclaircies", 3: "☁️ Couvert",
        45: "🌫️ Brouillard", 48: "🌫️ Brouillard",
        51: "🌦️ Bruine", 53: "🌦️ Bruine", 55: "🌦️ Bruine", 56: "🌦️ Bruine", 57: "🌦️ Bruine",
        61: "🌧️ Pluie", 63: "🌧️ Pluie", 65: "🌧️ Pluie",
        66: "🌧️ Pluie", 67: "🌧️ Pluie", 80: "🌧️ Pluie", 81: "🌧️ Pluie", 82: "🌧️ Pluie",
        71: "❄️ Neige", 73: "❄️ Neige", 75: "❄️ Neige", 77: "❄️ Neige",
        85: "❄️ Neige", 86: "❄️ Neige",
        95: "⛈️ Orage", 96: "⛈️ Orage", 99: "⛈️ Orage",
    }
    return mapping.get(code, "❓ Inconnu")


def estimer_watts(pente_pct, vitesse_kmh, poids_total_kg=75):
    g = 9.81
    vitesse_ms = vitesse_kmh / 3.6
    pente_rad = math.atan(pente_pct / 100)
    # Composante gravitationnelle + résistance au roulement estimée
    p_gravite = poids_total_kg * g * math.sin(pente_rad) * vitesse_ms
    p_roulement = poids_total_kg * g * 0.004 * vitesse_ms
    return max(0, int(p_gravite + p_roulement))


# ==============================================================================
# SECTION 2 : CATÉGORISATION INSPIRÉE UCI — ADAPTÉE LOISIR
# ==============================================================================
# Principe UCI : score = (dénivelé_m) * (pente_moy_%) — avec des seuils empiriques.
# Adaptations loisir :
#   - On abaisse les seuils pour valoriser les cols de "petit" cyclisme
#   - On exige un minimum de 20m de D+ et 300m de long pour éviter le bruit
#   - Pas de prise en compte de la position dans l'étape (simplification volontaire)

SEUILS_UCI_LOISIR = {
    "🔴 HC":          2000,   # > 2000 : Hors Catégorie  (ex : Ventoux, Galibier)
    "🟠 1ère Cat.":    800,   # 800–2000 : 1ère catégorie (ex : col de la Croix-de-Fer)
    "🟡 2ème Cat.":    350,   # 350–800  : 2ème catégorie (ex : col d'Izoard court versant)
    "🟢 3ème Cat.":    120,   # 120–350  : 3ème catégorie
    "🔵 4ème Cat.":     40,   #  40–120  : 4ème catégorie
    "⚪ Non classée":    0,   #   0–40   : côte non classée (affichée quand même)
}

def categoriser_ascension_uci_loisir(distance_m, d_plus):
    """
    Score = d_plus (m) × pente_moyenne (%)
    Retourne (catégorie, score) ou (None, 0) si pas qualifiable.
    """
    if distance_m < 300 or d_plus < 20:
        return None, 0
    pente_moy = (d_plus / distance_m) * 100
    if pente_moy < 1.0:   # faux-plat inférieur à 1% → ignoré
        return None, 0
    score = d_plus * pente_moy
    for label, seuil in SEUILS_UCI_LOISIR.items():
        if score >= seuil:
            return label, round(score, 1)
    return None, 0


# ==============================================================================
# SECTION 3 : DÉTECTION DES ASCENSIONS — ALGORITHME MULTI-PASSES
# ==============================================================================
# Problèmes de l'algo précédent :
#   1. Ratait les courtes montées raides (seuil de démarrage trop haut)
#   2. Ratait les faux-plats longs (tolérance de fin trop serrée)
#   3. Coupait les cols avec un replat au milieu
#
# Solution : 3 passes + lissage préalable
#   - Passe 1 : lissage de l'altitude (moyenne mobile) pour effacer le bruit GPS
#   - Passe 2 : détection des segments montants bruts
#   - Passe 3 : fusion des segments séparés par un replat < FUSION_MAX_M de descente

LISSAGE_FENETRE = 5       # Points de lissage (médiane mobile)
SEUIL_DEBUT_M   = 10      # Gain mini pour démarrer une montée (m)
MARGE_FIN_M     = 30      # Descente depuis le sommet pour clore une montée (m)
FUSION_MAX_M    = 25      # Descente maxi pour fusionner deux segments (m)


def lisser_altitude(alts, fenetre=5):
    """Lissage par moyenne mobile symétrique."""
    demi = fenetre // 2
    n = len(alts)
    result = []
    for i in range(n):
        start = max(0, i - demi)
        end   = min(n, i + demi + 1)
        result.append(sum(alts[start:end]) / (end - start))
    return result


def detecter_segments_montants(dists, alts_lisses):
    """
    Passe 2 : retourne une liste de segments (debut_idx, sommet_idx).
    Un segment démarre quand on gagne SEUIL_DEBUT_M depuis le dernier creux.
    Il se termine quand on descend de MARGE_FIN_M depuis le sommet courant.
    """
    segments = []
    n = len(alts_lisses)
    en_montee = False
    creux_idx = 0
    sommet_idx = 0

    for i in range(1, n):
        alt = alts_lisses[i]
        if not en_montee:
            if alt < alts_lisses[creux_idx]:
                creux_idx = i
            elif alt >= alts_lisses[creux_idx] + SEUIL_DEBUT_M:
                en_montee = True
                sommet_idx = i
        else:
            if alt > alts_lisses[sommet_idx]:
                sommet_idx = i
            elif alt <= alts_lisses[sommet_idx] - MARGE_FIN_M:
                segments.append((creux_idx, sommet_idx))
                en_montee = False
                creux_idx = i
                sommet_idx = i

    if en_montee and sommet_idx > creux_idx:
        segments.append((creux_idx, sommet_idx))

    return segments


def fusionner_segments(segments, alts_lisses):
    """
    Passe 3 : fusionne deux segments consécutifs si la descente entre eux
    est inférieure à FUSION_MAX_M (replat ou col intermédiaire).
    """
    if not segments:
        return []
    fusionnes = [segments[0]]
    for debut, sommet in segments[1:]:
        prev_debut, prev_sommet = fusionnes[-1]
        descente_inter = alts_lisses[prev_sommet] - alts_lisses[debut]
        if descente_inter <= FUSION_MAX_M:
            # On fusionne : on garde le début du précédent et le sommet le plus haut
            nouveau_sommet = sommet if alts_lisses[sommet] >= alts_lisses[prev_sommet] else prev_sommet
            fusionnes[-1] = (prev_debut, nouveau_sommet)
        else:
            fusionnes.append((debut, sommet))
    return fusionnes


def calculer_pente_max(dists, alts, debut_idx, sommet_idx, fenetre_km=0.05):
    """Calcule la pente max sur une fenêtre glissante de fenetre_km km."""
    pente_max = 0.0
    for i in range(debut_idx + 1, sommet_idx + 1):
        for j in range(i - 1, max(debut_idx - 1, i - 50), -1):
            dist_diff = (dists[i] - dists[j]) * 1000
            if dist_diff >= fenetre_km * 1000:
                alt_diff = alts[i] - alts[j]
                pente = (alt_diff / dist_diff) * 100
                if 0 < pente <= 40:
                    pente_max = max(pente_max, pente)
                break
    return round(pente_max, 1)


def detecter_ascensions(df_profil):
    """
    Détecte toutes les ascensions dans le profil altimétrique.
    Retourne une liste de dicts avec les stats de chaque montée.
    """
    if df_profil.empty or len(df_profil) < 3:
        return []

    alts  = df_profil["Altitude (m)"].tolist()
    dists = df_profil["Distance (km)"].tolist()

    # Passe 1 : lissage
    alts_lisses = lisser_altitude(alts, LISSAGE_FENETRE)

    # Passe 2 : détection
    segments = detecter_segments_montants(dists, alts_lisses)

    # Passe 3 : fusion
    segments = fusionner_segments(segments, alts_lisses)

    ascensions = []
    for debut_idx, sommet_idx in segments:
        dist_debut  = dists[debut_idx]
        dist_sommet = dists[sommet_idx]
        alt_debut   = alts[debut_idx]   # altitude réelle (non lissée)
        alt_sommet  = alts[sommet_idx]

        dist_km = dist_sommet - dist_debut
        d_plus  = alt_sommet - alt_debut

        if dist_km <= 0 or d_plus <= 0:
            continue

        cat, score = categoriser_ascension_uci_loisir(dist_km * 1000, d_plus)
        if cat is None:
            continue

        pente_moy = (d_plus / (dist_km * 1000)) * 100
        pente_max = calculer_pente_max(dists, alts, debut_idx, sommet_idx)

        ascensions.append({
            # Colonnes affichées
            "Catégorie":       cat,
            "Départ (km)":     round(dist_debut, 1),
            "Sommet (km)":     round(dist_sommet, 1),
            "Longueur":        f"{round(dist_km, 1)} km",
            "Dénivelé":        f"{int(d_plus)} m",
            "Pente moy.":      f"{round(pente_moy, 1)} %",
            "Pente max":       f"{round(pente_max, 1)} %",
            "Alt. sommet":     f"{int(alt_sommet)} m",
            "Score UCI":       score,
            # Valeurs internes pour graphique et puissance
            "_debut_km":       dist_debut,
            "_sommet_km":      dist_sommet,
            "_pente_moy":      pente_moy,
        })

    # Tri par position sur le parcours
    ascensions.sort(key=lambda x: x["_debut_km"])
    return ascensions


# ==============================================================================
# SECTION 4 : PARSING GPX + MÉTÉO (avec cache)
# ==============================================================================

@st.cache_data(show_spinner=False)
def parser_gpx(contenu: bytes):
    try:
        gpx = gpxpy.parse(contenu)
        points = [p for track in gpx.tracks
                    for seg in track.segments
                    for p in seg.points]
        return points
    except Exception as e:
        logger.error(f"Erreur GPX : {e}")
        return []


@st.cache_data(show_spinner=False)
def recuperer_fuseau(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m&timezone=auto"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json().get("timezone", "UTC")
    except Exception as e:
        logger.warning(f"Fuseau horaire indisponible : {e}")
        return "UTC"


@st.cache_data(ttl=1800, show_spinner=False)
def recuperer_meteo_batch(checkpoints_frozen):
    if not checkpoints_frozen:
        return []
    lats = ",".join(str(cp[0]) for cp in checkpoints_frozen)
    lons = ",".join(str(cp[1]) for cp in checkpoints_frozen)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&hourly=temperature_2m,precipitation_probability,weathercode,"
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m&timezone=auto"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]
    except requests.exceptions.Timeout:
        logger.error("Timeout météo")
        return None
    except Exception as e:
        logger.error(f"Erreur météo : {e}")
        return None


def extraire_meteo(donnees_api, heure_api):
    vide = dict(Ciel="—", temp_val=None, Pluie="—",
                vent_val=None, rafales_val=None, Dir="—",
                dir_deg=None, effet="—")
    if not donnees_api or "hourly" not in donnees_api:
        return vide
    heures = donnees_api["hourly"].get("time", [])
    if heure_api not in heures:
        return vide
    idx = heures.index(heure_api)
    h = donnees_api["hourly"]
    def sg(k, d=None):
        v = h.get(k, [])
        return v[idx] if idx < len(v) else d
    dir_deg = sg("wind_direction_10m")
    dirs = ["N","NE","E","SE","S","SO","O","NO"]
    dir_label = dirs[round(dir_deg / 45) % 8] if dir_deg is not None else "—"
    return {
        "Ciel":      obtenir_icone_meteo(sg("weathercode", 0)),
        "temp_val":  sg("temperature_2m"),
        "Pluie":     f"{sg('precipitation_probability','—')}%",
        "vent_val":  sg("wind_speed_10m"),
        "rafales_val": sg("wind_gusts_10m"),
        "Dir":       dir_label,
        "dir_deg":   dir_deg,
        "effet":     "—",
    }


# ==============================================================================
# SECTION 5 : GRAPHIQUES PLOTLY
# ==============================================================================

COULEURS_CAT = {
    "🔴 HC":          "#ef4444",
    "🟠 1ère Cat.":   "#f97316",
    "🟡 2ème Cat.":   "#eab308",
    "🟢 3ème Cat.":   "#22c55e",
    "🔵 4ème Cat.":   "#3b82f6",
    "⚪ Non classée": "#94a3b8",
}


def creer_figure_profil(df_profil, ascensions, idx_survol=None):
    """
    Profil altimétrique Plotly interactif.
    idx_survol : index de l'ascension survolée (mise en avant en couleur).
    """
    fig = go.Figure()

    dists = df_profil["Distance (km)"].tolist()
    alts  = df_profil["Altitude (m)"].tolist()

    # Tracé de base
    fig.add_trace(go.Scatter(
        x=dists, y=alts,
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.15)",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="<b>Km %{x:.1f}</b><br>Altitude : %{y:.0f} m<extra></extra>",
        name="Profil",
    ))

    # Segments colorés par ascension
    for i, asc in enumerate(ascensions):
        d0   = asc["_debut_km"]
        d1   = asc["_sommet_km"]
        cat  = asc["Catégorie"]
        couleur = COULEURS_CAT.get(cat, "#94a3b8")
        opacite = 1.0 if idx_survol is None or idx_survol == i else 0.25

        # Masque des points dans le segment
        seg_x = [d for d in dists if d0 <= d <= d1]
        seg_y = [alts[j] for j, d in enumerate(dists) if d0 <= d <= d1]

        if not seg_x:
            continue

        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y,
            fill="tozeroy",
            fillcolor=f"rgba{tuple(int(couleur.lstrip('#')[k:k+2], 16) for k in (0,2,4)) + (round(opacite * 0.4, 2),)}",
            line=dict(color=couleur, width=3 if idx_survol == i else 2,
                      dash="solid"),
            opacity=opacite,
            hovertemplate=f"<b>{cat}</b><br>Km %{{x:.1f}}<br>Alt : %{{y:.0f}} m<extra></extra>",
            name=f"{cat} (Km {round(d0,1)})",
            showlegend=True,
        ))

        # Étiquette sommet
        fig.add_annotation(
            x=d1, y=seg_y[-1] if seg_y else 0,
            text=f"▲ {cat.split()[0]}",
            showarrow=True, arrowhead=2, arrowsize=0.8,
            arrowcolor=couleur, font=dict(size=10, color=couleur),
            bgcolor="white", bordercolor=couleur, borderwidth=1,
            opacity=opacite,
        )

    fig.update_layout(
        height=350,
        margin=dict(l=50, r=20, t=20, b=40),
        xaxis=dict(title="Distance (km)", showgrid=True, gridcolor="#e5e7eb"),
        yaxis=dict(title="Altitude (m)", showgrid=True, gridcolor="#e5e7eb"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    return fig


def creer_figure_vent(resultats_meteo):
    """Graphique vent + rafales interactif."""
    kms, vents, rafales, couleurs, effets = [], [], [], [], []

    for cp in resultats_meteo:
        v = cp.get("vent_val")
        if v is None:
            continue
        kms.append(cp["Km"])
        vents.append(v)
        rafales.append(cp.get("rafales_val") or v)
        effets.append(cp.get("effet", "—"))
        if v >= 40:   couleurs.append("#ef4444")
        elif v >= 25: couleurs.append("#f97316")
        elif v >= 10: couleurs.append("#eab308")
        else:         couleurs.append("#22c55e")

    fig = go.Figure()

    if kms:
        fig.add_trace(go.Bar(
            x=kms, y=vents,
            marker_color=couleurs,
            name="Vent moyen",
            hovertemplate="<b>Km %{x}</b><br>Vent : %{y} km/h<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=kms, y=rafales,
            mode="lines+markers",
            line=dict(color="#94a3b8", width=1.5, dash="dot"),
            marker=dict(size=5),
            name="Rafales",
            hovertemplate="<b>Km %{x}</b><br>Rafales : %{y} km/h<extra></extra>",
        ))

    fig.update_layout(
        height=220,
        margin=dict(l=50, r=20, t=10, b=40),
        xaxis=dict(title="Distance (km)", showgrid=True, gridcolor="#e5e7eb"),
        yaxis=dict(title="Vent (km/h)", showgrid=True, gridcolor="#e5e7eb", rangemode="tozero"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        barmode="overlay",
    )
    return fig


# ==============================================================================
# SECTION 6 : CARTE FOLIUM
# ==============================================================================

def creer_carte(points_gpx, resultats_meteo):
    carte = folium.Map(
        location=[points_gpx[0].latitude, points_gpx[0].longitude],
        zoom_start=11,
        tiles="CartoDB positron",
    )
    coords = [[p.latitude, p.longitude] for p in points_gpx]
    folium.PolyLine(coords, color="#3b82f6", weight=5, opacity=0.9).add_to(carte)

    folium.Marker(
        [points_gpx[0].latitude, points_gpx[0].longitude],
        tooltip="🚦 Départ",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(carte)
    folium.Marker(
        [points_gpx[-1].latitude, points_gpx[-1].longitude],
        tooltip="🏁 Arrivée",
        icon=folium.Icon(color="red", icon="flag", prefix="fa"),
    ).add_to(carte)

    for cp in resultats_meteo:
        t = cp.get("temp_val")
        if t is None:
            continue

        # ── Flèche vent SVG ──
        # dir_deg = direction D'OÙ vient le vent (météo convention).
        # On veut montrer vers où il souffle → +180°.
        dir_deg   = cp.get("dir_deg")
        vent_val  = cp.get("vent_val", 0) or 0
        if dir_deg is not None:
            rotation  = (dir_deg + 180) % 360   # sens du souffle
            # Couleur de la flèche selon intensité
            if vent_val >= 40:   fleche_couleur = "#ef4444"
            elif vent_val >= 25: fleche_couleur = "#f97316"
            elif vent_val >= 10: fleche_couleur = "#eab308"
            else:                fleche_couleur = "#22c55e"
            fleche_svg = (
                f'<svg width="28" height="28" viewBox="0 0 28 28" '
                f'style="vertical-align:middle;margin-right:4px">'
                f'<g transform="rotate({rotation},14,14)">'
                f'<polygon points="14,2 20,22 14,18 8,22" '
                f'fill="{fleche_couleur}" stroke="white" stroke-width="1.2"/>'
                f'</g></svg>'
            )
        else:
            fleche_svg = "🧭 "

        # ── Barre de progression pluie ──
        pluie_str = cp.get("Pluie", "—")
        try:
            pluie_pct = int(pluie_str.replace("%", "").strip())
        except (ValueError, AttributeError):
            pluie_pct = None

        if pluie_pct is not None:
            if pluie_pct >= 70:   pluie_couleur = "#3b82f6"
            elif pluie_pct >= 40: pluie_couleur = "#60a5fa"
            else:                 pluie_couleur = "#93c5fd"
            barre_pluie = (
                f'<div style="margin:4px 0 2px;font-size:11px;color:#374151">'
                f'&#127783;&#65039; Pluie : <b>{pluie_pct}%</b></div>'
                '<div style="background:#e5e7eb;border-radius:4px;height:7px;width:100%">'
                f'<div style="background:{pluie_couleur};width:{pluie_pct}%;height:7px;'
                'border-radius:4px"></div></div>'
            )
        else:
            barre_pluie = '<div style="font-size:11px;color:#374151">🌧️ Pluie : —</div>'

        popup_html = (
            '<div style="font-family:sans-serif;font-size:12px;min-width:200px">'
            '<div style="font-weight:700;font-size:13px;border-bottom:1px solid #e5e7eb;'
            'padding-bottom:4px;margin-bottom:6px">'
            f'{cp["Heure"]} — Km {cp["Km"]}'
            '</div>'
            f'<div style="color:#6b7280;margin-bottom:6px">⛰️ Alt : {cp["Alt (m)"]} m</div>'
            f'<div style="font-size:15px;margin-bottom:6px">{cp["Ciel"]} <b>{t}°C</b></div>'
            f'{barre_pluie}'
            '<div style="margin-top:8px;padding-top:6px;border-top:1px solid #f3f4f6">'
            '<div style="display:flex;align-items:center;margin-bottom:3px">'
            f'{fleche_svg}'
            f'<span><b>{vent_val} km/h</b>'
            f'<span style="color:#6b7280"> venant du {cp["Dir"]}</span></span>'
            '</div>'
            f'<div style="color:#6b7280;font-size:11px;margin-left:32px">'
            f'Rafales : {cp.get("rafales_val", "—")} km/h</div>'
            f'<div style="margin-top:4px;font-size:11px">🚴 <b>{cp.get("effet", "—")}</b></div>'
            '</div>'
            '</div>'
        )

        folium.Marker(
            [cp["lat"], cp["lon"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"{cp['Heure']} | {cp['Ciel']} {t}°C | 💨 {cp.get('vent_val','—')} km/h",
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(carte)

    return carte


# ==============================================================================
# SECTION 7 : APPLICATION PRINCIPALE
# ==============================================================================

def main():
    st.set_page_config(page_title="Vélo & Météo", page_icon="🚴‍♂️", layout="wide")

    st.title("🚴‍♂️ Mon Parcours Vélo & Météo")
    st.caption("Importez un fichier GPX pour analyser votre tracé : météo, cols, vent et profil interactif.")

    # ── SIDEBAR ──────────────────────────────────────────────────────────────
    st.sidebar.header("⚙️ Paramètres")
    date_dep   = st.sidebar.date_input("📅 Date de départ", value=date.today())
    heure_dep  = st.sidebar.time_input("🕐 Heure de départ")
    vitesse    = st.sidebar.number_input("🚴 Vitesse moyenne plat (km/h)", 5, 60, 25)

    st.sidebar.divider()
    ftp        = st.sidebar.number_input("⚡ FTP (W)", 50, 500, 220,
                    help="Puissance seuil fonctionnelle — sert à évaluer l'effort sur les cols.")
    poids      = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)

    st.sidebar.divider()
    intervalle = st.sidebar.selectbox(
        "⏱️ Intervalle checkpoints météo",
        options=[5, 10, 15], index=1,
        format_func=lambda x: f"Toutes les {x} min"
    )
    intervalle_sec = intervalle * 60
    ph_fuseau = st.sidebar.empty()
    ph_fuseau.info("🌍 Fuseau : en attente du tracé…")

    # ── IMPORT ───────────────────────────────────────────────────────────────
    st.divider()
    fichier = st.file_uploader("📂 Importez votre fichier parcours (.gpx)", type=["gpx"])
    if fichier is None:
        st.info("👆 Importez un fichier GPX pour commencer.")
        return

    with st.spinner("Lecture du fichier GPX…"):
        points_gpx = parser_gpx(fichier.read())

    if not points_gpx:
        st.error("❌ Fichier GPX vide ou corrompu.")
        return

    fuseau = recuperer_fuseau(points_gpx[0].latitude, points_gpx[0].longitude)
    ph_fuseau.success(f"🌍 **{fuseau}**")
    date_depart = datetime.combine(date_dep, heure_dep)

    # ── PHASE 1 : CALCULS PARCOURS ───────────────────────────────────────────
    checkpoints   = []
    profil_data   = []
    dist_tot_m    = 0.0
    d_plus_tot    = 0.0
    d_moins_tot   = 0.0
    temps_tot_sec = 0.0
    prochain_cp   = 0.0
    cap_actuel    = 0.0
    vitesse_ms    = (vitesse * 1000) / 3600

    for i in range(1, len(points_gpx)):
        p1, p2 = points_gpx[i-1], points_gpx[i]
        dist = p1.distance_2d(p2) or 0.0
        d_plus_local = 0.0

        if p1.elevation is not None and p2.elevation is not None:
            diff = p2.elevation - p1.elevation
            if diff > 0:
                d_plus_local  = diff
                d_plus_tot   += diff
            else:
                d_moins_tot  += abs(diff)

        dist_tot_m    += dist
        temps_tot_sec += (dist + d_plus_local * 10) / vitesse_ms
        cap_actuel     = calculer_cap(p1.latitude, p1.longitude, p2.latitude, p2.longitude)

        profil_data.append({
            "Distance (km)": round(dist_tot_m / 1000, 3),
            "Altitude (m)":  p2.elevation or 0,
        })

        if temps_tot_sec >= prochain_cp:
            hp = date_depart + timedelta(seconds=temps_tot_sec)
            checkpoints.append({
                "lat": p2.latitude, "lon": p2.longitude, "Cap": cap_actuel,
                "Heure":     hp.strftime("%d/%m %H:%M"),
                "Heure_API": hp.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
                "Km":        round(dist_tot_m / 1000, 1),
                "Alt (m)":   int(p2.elevation) if p2.elevation else 0,
            })
            prochain_cp += intervalle_sec

    heure_arr = date_depart + timedelta(seconds=temps_tot_sec)
    pf = points_gpx[-1]
    checkpoints.append({
        "lat": pf.latitude, "lon": pf.longitude, "Cap": cap_actuel,
        "Heure":     heure_arr.strftime("%d/%m %H:%M") + " 🏁",
        "Heure_API": heure_arr.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
        "Km":        round(dist_tot_m / 1000, 1),
        "Alt (m)":   int(pf.elevation) if pf.elevation else 0,
    })

    df_profil = pd.DataFrame(profil_data)

    # ── PHASE 2 : ASCENSIONS ─────────────────────────────────────────────────
    ascensions = detecter_ascensions(df_profil)

    # ── PHASE 3 : MÉTÉO ──────────────────────────────────────────────────────
    frozen = tuple((cp["lat"], cp["lon"], cp["Heure_API"]) for cp in checkpoints)
    with st.spinner("Récupération météo…"):
        rep_list = recuperer_meteo_batch(frozen)

    resultats_meteo = []
    erreur_meteo = rep_list is None

    if erreur_meteo:
        st.warning("⚠️ Météo indisponible (erreur réseau). Le reste de l'analyse reste affiché.")
        for cp in checkpoints:
            cp.update(Ciel="—", temp_val=None, Pluie="—",
                      vent_val=None, rafales_val=None, Dir="—", dir_deg=None, effet="—")
            resultats_meteo.append(cp)
    else:
        bar = st.progress(0, text="Traitement des checkpoints météo…")
        for i, cp in enumerate(checkpoints):
            m = extraire_meteo(rep_list[i] if i < len(rep_list) else {}, cp["Heure_API"])
            if m["dir_deg"] is not None:
                m["effet"] = direction_vent_relative(cp["Cap"], m["dir_deg"])
            cp.update(m)
            resultats_meteo.append(cp)
            bar.progress((i+1)/len(checkpoints), text=f"Checkpoint {i+1}/{len(checkpoints)}")
        bar.empty()
        st.success(f"✅ {len(resultats_meteo)} checkpoints météo traités.")

    # =========================================================================
    # AFFICHAGE
    # =========================================================================

    st.divider()

    # ── 1. CARTE (en premier, grande) ────────────────────────────────────────
    st.subheader("🗺️ Itinéraire & checkpoints météo")
    carte = creer_carte(points_gpx, resultats_meteo)
    st_folium(carte, width="100%", height=580, returned_objects=[])

    # ── 2. RÉSUMÉ ────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Résumé du parcours")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📏 Distance",        f"{round(dist_tot_m/1000,1)} km")
    c2.metric("⬆️ Dénivelé +",      f"{int(d_plus_tot)} m")
    c3.metric("⬇️ Dénivelé −",      f"{int(d_moins_tot)} m")
    c4.metric("⏱️ Durée estimée",   f"{int(temps_tot_sec//3600)}h{int((temps_tot_sec%3600)//60):02d}m")
    c5.metric("🏁 Arrivée estimée", heure_arr.strftime("%H:%M"))

    # ── 3. PROFIL ALTIMÉTRIQUE INTERACTIF ────────────────────────────────────
    st.divider()
    st.subheader("⛰️ Profil altimétrique interactif")
    st.caption("Zoomez, déplacez-vous, survolez pour voir l'altitude. "
               "Sélectionnez une côte dans le tableau ci-dessous pour la mettre en avant.")

    if not df_profil.empty:
        # Sélection d'une ascension pour surbrillance
        idx_survol = None
        if ascensions:
            noms = ["(aucune sélection)"] + [
                f"{a['Catégorie']} — Km {a['Départ (km)']}→{a['Sommet (km)']} ({a['Longueur']})"
                for a in ascensions
            ]
            choix = st.selectbox(
                "🔍 Mettre en avant une côte sur le profil :",
                options=noms, index=0,
                help="Sélectionnez une montée pour la colorer sur le profil altimétrique."
            )
            if choix != "(aucune sélection)":
                idx_survol = noms.index(choix) - 1  # -1 car on a "(aucune)"

        fig_profil = creer_figure_profil(df_profil, ascensions, idx_survol)
        st.plotly_chart(fig_profil, use_container_width=True)

    # ── 4. VENT ───────────────────────────────────────────────────────────────
    if not erreur_meteo:
        st.subheader("💨 Vent sur le parcours")
        st.caption("Barres colorées = intensité du vent moyen. Pointillés = rafales.")
        fig_vent = creer_figure_vent(resultats_meteo)
        st.plotly_chart(fig_vent, use_container_width=True)

        # Légende couleurs vent
        col_leg = st.columns(4)
        col_leg[0].markdown("🟢 **< 10 km/h** — Faible")
        col_leg[1].markdown("🟡 **10–25 km/h** — Léger")
        col_leg[2].markdown("🟠 **25–40 km/h** — Modéré")
        col_leg[3].markdown("🔴 **> 40 km/h** — Fort")

    # ── 5. TABLEAU ASCENSIONS ────────────────────────────────────────────────
    st.divider()
    st.subheader("🏔️ Analyse des ascensions")
    st.caption(
        "**Catégorisation inspirée UCI adaptée loisir.** "
        "Score = Dénivelé (m) × Pente moyenne (%). "
        "⚪ Non classée < 40 · 🔵 4ème Cat. 40–120 · 🟢 3ème Cat. 120–350 · "
        "🟡 2ème Cat. 350–800 · 🟠 1ère Cat. 800–2000 · 🔴 HC > 2000"
    )

    if ascensions:
        for a in ascensions:
            w = estimer_watts(a["_pente_moy"], vitesse, poids)
            pct = round((w / ftp) * 100) if ftp > 0 else 0
            a["Puissance"] = f"{w} W"
            a["% FTP"]     = f"{pct} %"
            a["Effort"]    = (
                "🔴 Max"       if pct > 105 else
                "🟠 Très dur"  if pct > 95  else
                "🟡 Difficile" if pct > 80  else
                "🟢 Modéré"    if pct > 60  else
                "🔵 Endurance"
            )

        cols_aff = [
            "Catégorie", "Départ (km)", "Sommet (km)", "Longueur",
            "Dénivelé", "Pente moy.", "Pente max", "Alt. sommet",
            "Score UCI", "Puissance", "% FTP", "Effort"
        ]
        df_asc = pd.DataFrame(ascensions)[cols_aff]
        st.dataframe(
            df_asc,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Catégorie":    st.column_config.TextColumn("Catégorie", help="Classification inspirée UCI loisir"),
                "Départ (km)":  st.column_config.NumberColumn("Départ (km)", help="Kilomètre de début de la montée"),
                "Sommet (km)":  st.column_config.NumberColumn("Sommet (km)", help="Kilomètre du sommet"),
                "Longueur":     st.column_config.TextColumn("Longueur", help="Distance totale de la montée"),
                "Dénivelé":     st.column_config.TextColumn("D+", help="Dénivelé positif de la montée"),
                "Pente moy.":   st.column_config.TextColumn("Pente moy.", help="Pente moyenne sur toute la montée"),
                "Pente max":    st.column_config.TextColumn("Pente max", help="Pente maximale sur 50m"),
                "Alt. sommet":  st.column_config.TextColumn("Alt. sommet", help="Altitude au sommet"),
                "Score UCI":    st.column_config.NumberColumn("Score UCI", help="D+ × pente moy. — sert à la catégorisation"),
                "Puissance":    st.column_config.TextColumn("Puissance est.", help=f"Watts estimés à {vitesse} km/h pour {poids} kg"),
                "% FTP":        st.column_config.TextColumn("% FTP", help=f"Pourcentage de votre FTP ({ftp} W)"),
                "Effort":       st.column_config.TextColumn("Effort", help="Intensité estimée de l'effort"),
            }
        )
    else:
        st.success("🚴‍♂️ Parcours roulant — aucune difficulté catégorisée détectée !")

    # ── 6. TABLEAU MÉTÉO ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("⏱️ Conditions météo détaillées")
    st.caption(
        "Un point toutes les **{} min** de riding. "
        "**Effet Vent** : ⬇️ Face = freine · ⬆️ Dos = aide · ↘️↙️ Côté = déstabilise.".format(intervalle)
    )

    if resultats_meteo:
        lignes = []
        for cp in resultats_meteo:
            t = cp.get("temp_val")
            lignes.append({
                "Heure":       cp["Heure"],
                "Km":          cp["Km"],
                "Alt (m)":     cp["Alt (m)"],
                "Ciel":        cp.get("Ciel", "—"),
                "Temp (°C)":   f"{t}°C" if t is not None else "—",
                "Pluie":       cp.get("Pluie", "—"),
                "Vent (km/h)": cp.get("vent_val") or "—",
                "Rafales":     cp.get("rafales_val") or "—",
                "Direction":   cp.get("Dir", "—"),
                "Effet vent":  cp.get("effet", "—"),
            })
        df_meteo = pd.DataFrame(lignes)
        st.dataframe(
            df_meteo,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Heure":       st.column_config.TextColumn("🕐 Heure",      help="Heure de passage estimée à ce point"),
                "Km":          st.column_config.NumberColumn("📏 Km",        help="Distance depuis le départ"),
                "Alt (m)":     st.column_config.NumberColumn("⛰️ Alt (m)",   help="Altitude à ce point"),
                "Ciel":        st.column_config.TextColumn("🌤️ Ciel",       help="Conditions générales"),
                "Temp (°C)":   st.column_config.TextColumn("🌡️ Temp",       help="Température à 2m du sol"),
                "Pluie":       st.column_config.TextColumn("🌧️ Pluie",      help="Probabilité de précipitations"),
                "Vent (km/h)": st.column_config.TextColumn("💨 Vent",        help="Vitesse du vent moyen à 10m"),
                "Rafales":     st.column_config.TextColumn("🌬️ Rafales",    help="Vitesse des rafales"),
                "Direction":   st.column_config.TextColumn("🧭 Direction",   help="Direction d'où vient le vent"),
                "Effet vent":  st.column_config.TextColumn("🚴 Effet vent",  help="Ressenti du vent pour le cycliste selon son cap"),
            }
        )


if __name__ == "__main__":
    main()
