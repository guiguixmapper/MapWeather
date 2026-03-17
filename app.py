import streamlit as st
import pandas as pd
import gpxpy
import folium
from streamlit_folium import st_folium
import requests
from datetime import datetime, timedelta, date
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import math
import logging

# --- CONFIGURATION DU LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# SECTION 1 : FONCTIONS UTILITAIRES ET CALCULS
# ==============================================================================

def calculer_cap(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcule le cap (bearing) entre deux points GPS en degrés (0-360)."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def direction_vent_relative(cap_velo: float, dir_vent: float) -> str:
    """Retourne l'effet du vent ressenti par le cycliste selon son cap et la direction du vent."""
    diff = (dir_vent - cap_velo) % 360
    if diff <= 45 or diff >= 315:
        return "⬇️ Face"
    elif 135 <= diff <= 225:
        return "⬆️ Dos"
    elif 45 < diff < 135:
        return "↘️ Côté (Droit)"
    else:
        return "↙️ Côté (Gauche)"


def obtenir_icone_meteo(code: int) -> str:
    """Convertit un code météo WMO en emoji + libellé."""
    mapping = {
        0: "☀️ Clair",
        1: "⛅ Éclaircies", 2: "⛅ Éclaircies",
        3: "☁️ Couvert",
        45: "🌫️ Brouillard", 48: "🌫️ Brouillard",
        51: "🌦️ Bruine", 53: "🌦️ Bruine", 55: "🌦️ Bruine",
        56: "🌦️ Bruine", 57: "🌦️ Bruine",
        61: "🌧️ Pluie", 63: "🌧️ Pluie", 65: "🌧️ Pluie",
        66: "🌧️ Pluie", 67: "🌧️ Pluie",
        71: "❄️ Neige", 73: "❄️ Neige", 75: "❄️ Neige",
        77: "❄️ Neige", 85: "❄️ Neige", 86: "❄️ Neige",
        80: "🌧️ Pluie", 81: "🌧️ Pluie", 82: "🌧️ Pluie",
        95: "⛈️ Orage", 96: "⛈️ Orage", 99: "⛈️ Orage",
    }
    return mapping.get(code, "❓ Inconnu")


def categoriser_ascension(distance_m: float, d_plus: float) -> str | None:
    """
    Catégorise une ascension selon un score inspiré du Tour de France.
    Retourne None si la montée n'est pas qualifiable.
    """
    if distance_m < 500 or d_plus < 30:
        return None
    pente_moyenne = (d_plus / distance_m) * 100
    if pente_moyenne < 1.5:
        return None
    score = (distance_m / 1000) * (pente_moyenne ** 2)
    if score >= 250:
        return "🔴 HC"
    elif score >= 150:
        return "🟠 1ère Cat."
    elif score >= 80:
        return "🟡 2ème Cat."
    elif score >= 35:
        return "🟢 3ème Cat."
    elif score >= 15:
        return "🔵 4ème Cat."
    return None


def estimer_watts_ascension(pente_pct: float, vitesse_kmh: float,
                             poids_total_kg: float = 75) -> int:
    """
    Estimation simplifiée de la puissance développée en montée (W).
    P ≈ poids * g * sin(arctan(pente)) * vitesse
    """
    g = 9.81
    pente_rad = math.atan(pente_pct / 100)
    vitesse_ms = vitesse_kmh / 3.6
    return int(poids_total_kg * g * math.sin(pente_rad) * vitesse_ms)


# ==============================================================================
# SECTION 2 : PARSING GPX (avec cache)
# ==============================================================================

@st.cache_data(show_spinner=False)
def parser_gpx(contenu_gpx: bytes) -> list:
    """Parse un fichier GPX et retourne la liste des points. Mis en cache."""
    try:
        gpx = gpxpy.parse(contenu_gpx)
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append(point)
        return points
    except Exception as e:
        logger.error(f"Erreur parsing GPX : {e}")
        return []


# ==============================================================================
# SECTION 3 : DÉTECTION DES ASCENSIONS (refactorisée)
# ==============================================================================

def detecter_ascensions(df_profil: pd.DataFrame) -> list:
    """
    Détecte et catégorise les ascensions dans un profil altimétrique.
    
    Algorithme à fenêtre glissante :
    - On cherche des segments où l'altitude monte de façon continue.
    - Une montée démarre quand on gagne > SEUIL_DEBUT_M mètres depuis le dernier bas.
    - Une montée se termine quand on redescend de > MARGE_FIN_M mètres depuis le sommet.
    """
    SEUIL_DEBUT_M = 15    # Gain minimum pour déclencher une montée
    MARGE_FIN_M = 40      # Perte depuis le sommet pour valider la fin
    FENETRE_PENTE_KM = 0.05  # Fenêtre de calcul de la pente max (50m)

    ascensions = []
    alts = df_profil["Altitude (m)"].values
    dists = df_profil["Distance (km)"].values
    n = len(alts)

    if n < 2:
        return ascensions

    en_montee = False
    debut_idx = 0
    sommet_idx = 0
    alt_min_local = alts[0]

    for i in range(1, n):
        alt = alts[i]

        if not en_montee:
            if alt < alt_min_local:
                alt_min_local = alt
                debut_idx = i
            elif alt > alt_min_local + SEUIL_DEBUT_M:
                en_montee = True
                sommet_idx = i
        else:
            if alt > alts[sommet_idx]:
                sommet_idx = i
            elif alt <= alts[sommet_idx] - MARGE_FIN_M:
                # La montée est terminée — on l'analyse
                _enregistrer_ascension(
                    ascensions, df_profil, debut_idx, sommet_idx,
                    dists, alts, FENETRE_PENTE_KM
                )
                # Réinitialisation pour la prochaine montée
                en_montee = False
                alt_min_local = alt
                debut_idx = i

    # Cas : montée encore en cours à la fin du parcours
    if en_montee:
        _enregistrer_ascension(
            ascensions, df_profil, debut_idx, sommet_idx,
            dists, alts, FENETRE_PENTE_KM
        )

    return ascensions


def _enregistrer_ascension(ascensions: list, df_profil: pd.DataFrame,
                            debut_idx: int, sommet_idx: int,
                            dists, alts, fenetre_km: float):
    """Calcule les statistiques d'une ascension et l'ajoute à la liste si catégorisée."""
    dist_debut = dists[debut_idx]
    dist_sommet = dists[sommet_idx]
    alt_debut = alts[debut_idx]
    alt_sommet = alts[sommet_idx]

    dist_totale_km = dist_sommet - dist_debut
    d_plus = alt_sommet - alt_debut

    if dist_totale_km <= 0 or d_plus <= 0:
        return

    cat = categoriser_ascension(dist_totale_km * 1000, d_plus)
    if cat is None:
        return

    # Calcul de la pente max sur une fenêtre glissante
    pente_max = 0.0
    for i in range(debut_idx + 1, sommet_idx + 1):
        # Cherche le point précédent à ~fenetre_km de distance
        for j in range(i - 1, debut_idx - 1, -1):
            if (dists[i] - dists[j]) >= fenetre_km:
                alt_diff = alts[i] - alts[j]
                dist_diff_m = (dists[i] - dists[j]) * 1000
                pente = (alt_diff / dist_diff_m) * 100
                if 0 < pente <= 40:
                    pente_max = max(pente_max, pente)
                break

    pente_moy = (d_plus / (dist_totale_km * 1000)) * 100

    ascensions.append({
        "Départ": f"Km {round(dist_debut, 1)}",
        "Sommet": f"Km {round(dist_sommet, 1)}",
        "Catégorie": cat,
        "Distance": f"{round(dist_totale_km, 1)} km",
        "Pente Moy.": f"{round(pente_moy, 1)} %",
        "Pente Max": f"{round(pente_max, 1)} %",
        "Dénivelé": f"{int(d_plus)} m",
        "Alt. sommet": f"{int(alt_sommet)} m",
        # Valeurs numériques pour usage interne
        "_pente_moy": pente_moy,
        "_dist_km": dist_totale_km,
        "_d_plus": d_plus,
    })


# ==============================================================================
# SECTION 4 : APPEL MÉTÉO (avec cache)
# ==============================================================================

@st.cache_data(ttl=1800, show_spinner=False)  # Cache 30 minutes
def recuperer_meteo_batch(checkpoints_frozen: tuple) -> list | None:
    """
    Récupère la météo pour tous les checkpoints en un seul appel API batch.
    Mis en cache 30 minutes pour éviter les appels répétés.
    
    Args:
        checkpoints_frozen: tuple de tuples (lat, lon, heure_api) — hashable pour le cache.
    
    Returns:
        Liste de dicts météo ou None en cas d'erreur.
    """
    if not checkpoints_frozen:
        return []

    lats = ",".join([str(cp[0]) for cp in checkpoints_frozen])
    lons = ",".join([str(cp[1]) for cp in checkpoints_frozen])
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        f"&hourly=temperature_2m,precipitation_probability,weathercode,"
        f"wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        f"&timezone=auto"
    )

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else [data]
    except requests.exceptions.Timeout:
        logger.error("Timeout lors de l'appel météo")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"Erreur HTTP météo : {e}")
        return None
    except Exception as e:
        logger.error(f"Erreur inattendue météo : {e}")
        return None


@st.cache_data(show_spinner=False)
def recuperer_fuseau(lat: float, lon: float) -> str:
    """Récupère le fuseau horaire d'un point GPS via Open-Meteo."""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m&timezone=auto"
        rep = requests.get(url, timeout=10)
        rep.raise_for_status()
        return rep.json().get("timezone", "UTC")
    except Exception as e:
        logger.warning(f"Impossible de récupérer le fuseau horaire : {e}")
        return "UTC"


def extraire_meteo_checkpoint(donnees_api: dict, heure_api: str) -> dict:
    """Extrait les données météo pour une heure donnée depuis la réponse API."""
    vide = {
        "Ciel": "—", "Temp (°C)": "—", "Pluie": "—",
        "Vent (km/h)": None, "Rafales": None,
        "Dir.": "—", "dir_vent_deg": None, "Effet Vent": "—"
    }

    if "hourly" not in donnees_api:
        return vide

    heures = donnees_api["hourly"].get("time", [])
    if heure_api not in heures:
        logger.warning(f"Heure {heure_api} absente des données météo")
        return vide

    idx = heures.index(heure_api)
    h = donnees_api["hourly"]

    def safe_get(key, default=None):
        val = h.get(key, [])
        return val[idx] if idx < len(val) else default

    vent_dir = safe_get("wind_direction_10m")
    directions = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    dir_label = directions[round(vent_dir / 45) % 8] if vent_dir is not None else "—"

    return {
        "Ciel": obtenir_icone_meteo(safe_get("weathercode", 0)),
        "Temp (°C)": f"{safe_get('temperature_2m', '—')}°",
        "Pluie": f"{safe_get('precipitation_probability', '—')}%",
        "Vent (km/h)": safe_get("wind_speed_10m"),
        "Rafales": safe_get("wind_gusts_10m"),
        "Dir.": dir_label,
        "dir_vent_deg": vent_dir,
        "Effet Vent": "—",
    }


# ==============================================================================
# SECTION 5 : GRAPHIQUES
# ==============================================================================

def creer_graphique_profil_vent(df_profil: pd.DataFrame, resultats_meteo: list) -> plt.Figure:
    """
    Crée une figure avec deux graphiques superposés :
    - Profil altimétrique
    - Vitesse et direction du vent sur le parcours
    """
    fig = plt.figure(figsize=(12, 6))
    gs = gridspec.GridSpec(2, 1, height_ratios=[2, 1], hspace=0.05)

    # --- Graphique 1 : Profil altimétrique ---
    ax1 = fig.add_subplot(gs[0])
    ax1.fill_between(
        df_profil["Distance (km)"], df_profil["Altitude (m)"],
        color="#3b82f6", alpha=0.25
    )
    ax1.plot(
        df_profil["Distance (km)"], df_profil["Altitude (m)"],
        color="#3b82f6", linewidth=2, label="Altitude"
    )
    ax1.set_ylabel("Altitude (m)", color="#4b5563", fontsize=10)
    ax1.tick_params(axis="x", labelbottom=False)
    ax1.tick_params(colors="#6b7280")
    ax1.grid(True, linestyle="--", alpha=0.4)
    ax1.set_xlim(df_profil["Distance (km)"].min(), df_profil["Distance (km)"].max())

    # --- Graphique 2 : Vent ---
    kms_vent = []
    vents = []
    rafales = []
    couleurs_vent = []

    for cp in resultats_meteo:
        if cp.get("Vent (km/h)") is not None:
            kms_vent.append(cp["Km"])
            v = cp["Vent (km/h)"]
            r = cp.get("Rafales") or v
            vents.append(v)
            rafales.append(r)
            # Couleur selon intensité du vent
            if v >= 40:
                couleurs_vent.append("#ef4444")   # Rouge : fort
            elif v >= 25:
                couleurs_vent.append("#f97316")   # Orange : modéré
            elif v >= 10:
                couleurs_vent.append("#eab308")   # Jaune : léger
            else:
                couleurs_vent.append("#22c55e")   # Vert : faible

    ax2 = fig.add_subplot(gs[1], sharex=ax1)

    if kms_vent:
        ax2.bar(kms_vent, vents, width=0.8, color=couleurs_vent,
                alpha=0.8, label="Vent moy.", zorder=2)
        ax2.plot(kms_vent, rafales, color="#94a3b8", linewidth=1.5,
                 linestyle="--", marker=".", markersize=4, label="Rafales", zorder=3)

        # Légende de couleurs vent
        from matplotlib.patches import Patch
        legende = [
            Patch(color="#22c55e", label="< 10 km/h"),
            Patch(color="#eab308", label="10–25 km/h"),
            Patch(color="#f97316", label="25–40 km/h"),
            Patch(color="#ef4444", label="> 40 km/h"),
        ]
        ax2.legend(handles=legende, fontsize=7, loc="upper right", ncol=4)

    ax2.set_xlabel("Distance (km)", color="#4b5563", fontsize=10)
    ax2.set_ylabel("Vent (km/h)", color="#4b5563", fontsize=10)
    ax2.tick_params(colors="#6b7280")
    ax2.grid(True, linestyle="--", alpha=0.4)
    ax2.set_ylim(bottom=0)

    return fig


def creer_carte(points_gpx: list, resultats_meteo: list) -> folium.Map:
    """Crée la carte Folium avec le tracé et les marqueurs météo."""
    carte = folium.Map(
        location=[points_gpx[0].latitude, points_gpx[0].longitude],
        zoom_start=12,
        tiles="CartoDB positron"
    )

    coordonnees = [[p.latitude, p.longitude] for p in points_gpx]
    folium.PolyLine(coordonnees, color="#3b82f6", weight=5, opacity=0.9).add_to(carte)

    # Marqueur départ
    folium.Marker(
        location=[points_gpx[0].latitude, points_gpx[0].longitude],
        tooltip="🚦 Départ",
        icon=folium.Icon(color="green", icon="play", prefix="fa")
    ).add_to(carte)

    # Marqueur arrivée
    folium.Marker(
        location=[points_gpx[-1].latitude, points_gpx[-1].longitude],
        tooltip="🏁 Arrivée",
        icon=folium.Icon(color="red", icon="flag", prefix="fa")
    ).add_to(carte)

    # Marqueurs météo
    for cp in resultats_meteo:
        if cp.get("Temp (°C)") not in [None, "—", "Err"]:
            popup_html = (
                f"<b>{cp['Heure']} — Km {cp['Km']}</b><br>"
                f"<b>Alt :</b> {cp['Alt (m)']} m<br><br>"
                f"{cp['Ciel']} {cp['Temp (°C)']}<br>"
                f"💨 <b>Vent :</b> {cp['Vent (km/h)']} km/h ({cp['Dir.']})<br>"
                f"💨 <b>Rafales :</b> {cp.get('Rafales', '—')} km/h<br>"
                f"🌧️ <b>Pluie :</b> {cp['Pluie']}<br>"
                f"🚴 <b>Effet :</b> {cp['Effet Vent']}"
            )
            tooltip = f"{cp['Heure']} | {cp['Ciel']} {cp['Temp (°C)']} | 💨 {cp['Vent (km/h)']} km/h"
            folium.Marker(
                location=[cp["lat"], cp["lon"]],
                popup=folium.Popup(popup_html, max_width=260),
                tooltip=tooltip,
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(carte)

    return carte


# ==============================================================================
# SECTION 6 : APPLICATION PRINCIPALE
# ==============================================================================

def main():
    st.set_page_config(
        page_title="Vélo & Météo",
        page_icon="🚴‍♂️",
        layout="wide"
    )

    st.title("🚴‍♂️ Mon Parcours Vélo & Météo")
    st.caption("Analysez votre tracé GPX : météo en temps réel, profil altimétrique, cols et vent.")

    # --- SIDEBAR ---
    st.sidebar.header("⚙️ Paramètres")

    date_depart_choisie = st.sidebar.date_input("📅 Date de départ", value=date.today())
    heure_depart = st.sidebar.time_input("🕐 Heure de départ")
    vitesse_moyenne = st.sidebar.number_input(
        "🚴 Vitesse moyenne sur le plat (km/h)", min_value=5, max_value=60, value=25
    )

    st.sidebar.divider()

    ftp = st.sidebar.number_input(
        "⚡ FTP (Functional Threshold Power, en watts)", min_value=50, max_value=500, value=220,
        help="Votre puissance seuil fonctionnelle. Sert à contextualiser l'effort sur les cols."
    )
    poids_cycliste = st.sidebar.number_input(
        "⚖️ Poids cycliste + vélo (kg)", min_value=40, max_value=150, value=75,
        help="Poids total utilisé pour estimer la puissance en montée."
    )

    st.sidebar.divider()

    intervalle_min = st.sidebar.selectbox(
        "⏱️ Intervalle des checkpoints météo",
        options=[5, 10, 15],
        index=1,
        format_func=lambda x: f"Toutes les {x} min"
    )
    intervalle_sec = intervalle_min * 60

    placeholder_fuseau = st.sidebar.empty()
    placeholder_fuseau.info("🌍 Fuseau : en attente du tracé…")

    # --- IMPORT GPX ---
    st.divider()
    fichier_gpx = st.file_uploader(
        "📂 Importez votre fichier parcours (.gpx)", type=["gpx"]
    )

    if fichier_gpx is None:
        st.info("👆 Importez un fichier GPX pour commencer l'analyse.")
        return

    # ---- PARSING ----
    with st.spinner("Lecture du fichier GPX…"):
        contenu = fichier_gpx.read()
        points_gpx = parser_gpx(contenu)

    if not points_gpx:
        st.error("❌ Le fichier GPX semble vide ou corrompu. Vérifiez le fichier et réessayez.")
        return

    # ---- FUSEAU HORAIRE ----
    fuseau = recuperer_fuseau(points_gpx[0].latitude, points_gpx[0].longitude)
    placeholder_fuseau.success(f"🌍 Fuseau : **{fuseau}**")

    date_depart = datetime.combine(date_depart_choisie, heure_depart)

    # ========================================================
    # PHASE 1 : CALCULS DE BASE (distance, dénivelé, checkpoints)
    # ========================================================
    checkpoints = []
    profil_data = []
    distance_totale_m = 0.0
    d_plus_total = 0.0
    d_moins_total = 0.0
    temps_total_sec = 0.0
    prochain_checkpoint_sec = 0.0
    cap_actuel = 0.0

    vitesse_ms = (vitesse_moyenne * 1000) / 3600

    for i in range(1, len(points_gpx)):
        p1 = points_gpx[i - 1]
        p2 = points_gpx[i]

        dist = p1.distance_2d(p2) or 0.0
        d_plus_local = 0.0

        if p1.elevation is not None and p2.elevation is not None:
            diff_alt = p2.elevation - p1.elevation
            if diff_alt > 0:
                d_plus_local = diff_alt
                d_plus_total += diff_alt
            else:
                d_moins_total += abs(diff_alt)

        # Distance ajustée : +10m pour chaque mètre de dénivelé positif
        dist_ajustee = dist + (d_plus_local * 10)
        temps_sec = dist_ajustee / vitesse_ms if vitesse_ms > 0 else 0.0

        distance_totale_m += dist
        temps_total_sec += temps_sec
        cap_actuel = calculer_cap(p1.latitude, p1.longitude, p2.latitude, p2.longitude)

        profil_data.append({
            "Distance (km)": round(distance_totale_m / 1000, 3),
            "Altitude (m)": p2.elevation or 0
        })

        if temps_total_sec >= prochain_checkpoint_sec:
            heure_passage = date_depart + timedelta(seconds=temps_total_sec)
            checkpoints.append({
                "lat": p2.latitude,
                "lon": p2.longitude,
                "Cap": cap_actuel,
                "Heure": heure_passage.strftime("%d/%m %H:%M"),
                "Heure_API": heure_passage.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
                "Km": round(distance_totale_m / 1000, 1),
                "Alt (m)": int(p2.elevation) if p2.elevation else 0,
            })
            prochain_checkpoint_sec += intervalle_sec

    # Checkpoint d'arrivée
    heure_arrivee = date_depart + timedelta(seconds=temps_total_sec)
    p_final = points_gpx[-1]
    checkpoints.append({
        "lat": p_final.latitude,
        "lon": p_final.longitude,
        "Cap": cap_actuel,
        "Heure": heure_arrivee.strftime("%d/%m %H:%M") + " 🏁",
        "Heure_API": heure_arrivee.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
        "Km": round(distance_totale_m / 1000, 1),
        "Alt (m)": int(p_final.elevation) if p_final.elevation else 0,
    })

    df_profil = pd.DataFrame(profil_data)

    # ========================================================
    # PHASE 2 : ASCENSIONS
    # ========================================================
    ascensions = detecter_ascensions(df_profil)

    # ========================================================
    # PHASE 3 : MÉTÉO
    # ========================================================
    st.write("### 📡 Récupération des données météo…")

    # On convertit en tuple hashable pour le cache
    checkpoints_frozen = tuple(
        (cp["lat"], cp["lon"], cp["Heure_API"]) for cp in checkpoints
    )

    with st.spinner("Appel Open-Meteo en cours…"):
        rep_list = recuperer_meteo_batch(checkpoints_frozen)

    resultats_meteo = []
    erreur_meteo = False

    if rep_list is None:
        st.warning("⚠️ Impossible de récupérer la météo (erreur réseau ou API indisponible). Les données météo ne seront pas affichées.")
        erreur_meteo = True
        for cp in checkpoints:
            cp.update({"Ciel": "—", "Temp (°C)": "—", "Pluie": "—",
                       "Vent (km/h)": None, "Rafales": None,
                       "Dir.": "—", "dir_vent_deg": None, "Effet Vent": "—"})
            resultats_meteo.append(cp)
    else:
        barre = st.progress(0, text="Traitement des checkpoints…")
        for i, cp in enumerate(checkpoints):
            meteo = {}
            if i < len(rep_list):
                meteo = extraire_meteo_checkpoint(rep_list[i], cp["Heure_API"])
            else:
                meteo = {"Ciel": "—", "Temp (°C)": "—", "Pluie": "—",
                         "Vent (km/h)": None, "Rafales": None,
                         "Dir.": "—", "dir_vent_deg": None, "Effet Vent": "—"}

            # Calcul de l'effet vent
            if meteo.get("dir_vent_deg") is not None:
                meteo["Effet Vent"] = direction_vent_relative(cp["Cap"], meteo["dir_vent_deg"])

            cp.update(meteo)
            resultats_meteo.append(cp)
            barre.progress((i + 1) / len(checkpoints),
                           text=f"Checkpoint {i + 1}/{len(checkpoints)}")
        barre.empty()

    st.success(f"✅ {len(resultats_meteo)} checkpoints traités !")

    # ========================================================
    # PHASE 4 : RÉSUMÉ DU PARCOURS
    # ========================================================
    st.divider()
    st.write("### 📊 Résumé du parcours")

    duree_h = int(temps_total_sec // 3600)
    duree_m = int((temps_total_sec % 3600) // 60)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📏 Distance", f"{round(distance_totale_m / 1000, 1)} km")
    col2.metric("⬆️ Dénivelé +", f"{int(d_plus_total)} m")
    col3.metric("⬇️ Dénivelé −", f"{int(d_moins_total)} m")
    col4.metric("⏱️ Durée estimée", f"{duree_h}h {duree_m:02d}m")
    col5.metric("🏁 Arrivée estimée", heure_arrivee.strftime("%H:%M"))

    # ========================================================
    # PHASE 5 : CARTE
    # ========================================================
    st.divider()
    st.write("### 🗺️ Itinéraire & checkpoints météo")
    carte = creer_carte(points_gpx, resultats_meteo)
    st_folium(carte, width="100%", height=450, returned_objects=[])

    # ========================================================
    # PHASE 6 : PROFIL ALTIMÉTRIQUE + VENT
    # ========================================================
    st.divider()
    st.write("### ⛰️ Profil altimétrique & 💨 Vent sur le parcours")

    if not df_profil.empty:
        fig = creer_graphique_profil_vent(df_profil, resultats_meteo)
        st.pyplot(fig)
        plt.close(fig)
    else:
        st.warning("Pas de données d'altitude disponibles dans ce fichier GPX.")

    # ========================================================
    # PHASE 7 : ASCENSIONS
    # ========================================================
    st.divider()
    st.write("### 🏔️ Analyse des ascensions")

    if ascensions:
        # Ajout de l'estimation de puissance
        for a in ascensions:
            watts = estimer_watts_ascension(a["_pente_moy"], vitesse_moyenne, poids_cycliste)
            pct_ftp = round((watts / ftp) * 100) if ftp > 0 else 0
            a["Puissance estimée"] = f"{watts} W"
            a["% FTP"] = f"{pct_ftp} %"
            a["Effort"] = (
                "🔴 Max" if pct_ftp > 105 else
                "🟠 Très dur" if pct_ftp > 95 else
                "🟡 Difficile" if pct_ftp > 80 else
                "🟢 Modéré" if pct_ftp > 60 else
                "🔵 Endurance"
            )

        cols_affichage = [
            "Départ", "Sommet", "Catégorie", "Distance",
            "Pente Moy.", "Pente Max", "Dénivelé", "Alt. sommet",
            "Puissance estimée", "% FTP", "Effort"
        ]
        df_asc = pd.DataFrame(ascensions)[cols_affichage]
        st.dataframe(df_asc, use_container_width=True, hide_index=True)
    else:
        st.success("🚴‍♂️ Parcours plutôt roulant — aucune difficulté catégorisée détectée !")

    # ========================================================
    # PHASE 8 : TABLEAU MÉTÉO DÉTAILLÉ
    # ========================================================
    st.divider()
    st.write("### ⏱️ Conditions météo détaillées par checkpoint")

    cols_meteo = ["Heure", "Km", "Alt (m)", "Ciel", "Temp (°C)",
                  "Pluie", "Vent (km/h)", "Rafales", "Dir.", "Effet Vent"]
    
    # Filtrage des colonnes existantes
    cols_disponibles = [c for c in cols_meteo if c in resultats_meteo[0]]
    df_meteo = pd.DataFrame(resultats_meteo)[cols_disponibles]
    st.dataframe(df_meteo, use_container_width=True, hide_index=True)

    if erreur_meteo:
        st.info("ℹ️ Les données météo n'ont pas pu être récupérées. Réessayez dans quelques instants.")


# ==============================================================================
# POINT D'ENTRÉE
# ==============================================================================
if __name__ == "__main__":
    main()
