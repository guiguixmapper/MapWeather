import requests
from datetime import datetime, timedelta, date
import logging
import streamlit as st
import time

logger = logging.getLogger(__name__)

def recuperer_fuseau(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m&timezone=auto"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json().get("timezone", "UTC")
    except Exception as e:
        logger.warning(f"Fuseau horaire indisponible : {e}")
        return "UTC"

def recuperer_soleil(lat, lon, date_str):
    url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&date={date_str}&formatted=0"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json().get("results", {})
        lever   = datetime.fromisoformat(data["sunrise"])
        coucher = datetime.fromisoformat(data["sunset"])
        return {"lever": lever, "coucher": coucher}
    except Exception as e:
        logger.warning(f"Soleil indisponible : {e}")
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def recuperer_qualite_air(lat, lon, date_str):
    """Récupère l'indice UV et les pollens pour la journée"""
    url_aq = "https://air-quality-api.open-meteo.com/v1/air-quality"
    url_uv = "https://api.open-meteo.com/v1/forecast"
    
    resultat = {"uv_max": None, "pollen_alerte": "Aucune"}
    
    try:
        # 1. Qualité de l'air (Pollens)
        params_aq = {
            "latitude": lat, "longitude": lon,
            "hourly": "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen",
            "start_date": date_str, "end_date": date_str, "timezone": "auto"
        }
        res_aq = requests.get(url_aq, params=params_aq, timeout=10)
        if res_aq.status_code == 200:
            data = res_aq.json().get("hourly", {})
            pollens = []
            for p_type, label in [("grass_pollen", "Graminées"), ("birch_pollen", "Bouleau"), ("olive_pollen", "Olivier")]:
                vals = data.get(p_type, [])
                vals = [v for v in vals if v is not None]
                if vals and max(vals) > 50: # Seuil d'alerte arbitraire
                    pollens.append(label)
            if pollens:
                resultat["pollen_alerte"] = f"Élevé ({', '.join(pollens)})"

        # 2. UV Max (seulement dispo sur l'API forecast classique)
        params_uv = {
            "latitude": lat, "longitude": lon,
            "daily": "uv_index_max",
            "start_date": date_str, "end_date": date_str, "timezone": "auto"
        }
        res_uv = requests.get(url_uv, params=params_uv, timeout=10)
        if res_uv.status_code == 200:
            data = res_uv.json().get("daily", {})
            uvs = data.get("uv_index_max", [])
            if uvs and uvs[0] is not None:
                resultat["uv_max"] = round(uvs[0], 1)
                
    except Exception as e:
        logger.warning(f"Erreur Qualité Air/UV : {e}")
        
    return resultat

@st.cache_data(ttl=1800, show_spinner=False)
def recuperer_meteo_batch(checkpoints_figes, is_past=False, date_str=None):
    if not checkpoints_figes: return []

    lats = ",".join(str(cp[0]) for cp in checkpoints_figes)
    lons = ",".join(str(cp[1]) for cp in checkpoints_figes)

    if is_past and date_str:
        # MODE HISTORIQUE (Archives)
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": lats, "longitude": lons,
            "start_date": date_str, "end_date": date_str,
            "hourly": "temperature_2m,precipitation,weathercode,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "timezone": "auto"
        }
    else:
        # MODE PRÉVISION
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lats, "longitude": lons,
            "hourly": "temperature_2m,precipitation_probability,weathercode,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "timezone": "auto"
        }

    for tentative in range(3):
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 429:
                time.sleep(2); continue
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else [data]
        except Exception as e:
            logger.error(f"Erreur météo batch : {e}")
            break
    return None

def extraire_meteo(data_json, heure_api):
    vide = {"Ciel": "—", "temp_val": None, "Pluie": "—", "pluie_pct": None, "vent_val": None, "rafales_val": None, "Dir": "—", "dir_deg": None, "effet": "—", "ressenti": None}
    if not data_json or "hourly" not in data_json: return vide

    hourly = data_json["hourly"]
    times  = hourly.get("time", [])
    try: idx = times.index(heure_api)
    except ValueError: return vide

    wc = hourly.get("weathercode", [])[idx]
    t  = hourly.get("temperature_2m", [])[idx]
    
    # Gestion de la différence entre prévision et historique pour la pluie
    if "precipitation_probability" in hourly:
        pp = hourly.get("precipitation_probability", [])[idx]
    else:
        precip = hourly.get("precipitation", [])[idx]
        pp = 100 if (precip and precip > 0) else 0

    w  = hourly.get("wind_speed_10m", [])[idx]
    wd = hourly.get("wind_direction_10m", [])[idx]
    wg = hourly.get("wind_gusts_10m", [])[idx]

    icon = obtenir_icone_meteo(wc)
    DIR_WIND = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
    d_str = DIR_WIND[int((wd / 22.5) + 0.5) % 16] if wd is not None else "—"
    res = wind_chill(t, w) if (t is not None and w is not None) else None

    return {
        "Ciel": icon, "temp_val": t, "Pluie": f"{pp}%", "pluie_pct": pp,
        "vent_val": w, "rafales_val": wg, "Dir": d_str, "dir_deg": wd, "ressenti": res
    }

def direction_vent_relative(cap_velo, dir_vent):
    if cap_velo is None or dir_vent is None: return "—"
    diff = (dir_vent - cap_velo) % 360
    if diff > 180: diff -= 360
    if -45 <= diff <= 45: return "⬇️ Face"
    elif 135 <= diff or diff <= -135: return "⬆️ Dos"
    elif 45 < diff < 135: return "↙️ Côté (D)"
    else: return "↘️ Côté (G)"

def wind_chill(temp_c, vent_kmh):
    if temp_c <= 10 and vent_kmh > 4.8:
        wc = 13.12 + 0.6215 * temp_c - 11.37 * (vent_kmh**0.16) + 0.3965 * temp_c * (vent_kmh**0.16)
        return round(wc, 1)
    return None

def label_wind_chill(wc):
    if wc is None: return "—"
    if wc > 5: return f"{wc}°C (Frais)"
    elif wc > -5: return f"{wc}°C (Froid)"
    elif wc > -15: return f"{wc}°C (Très froid)"
    else: return f"{wc}°C (Glacial)"

def obtenir_icone_meteo(code):
    CODES = {
        0: "☀️ Clair", 1: "🌤️ Peu nuageux", 2: "⛅ Mi-couvert", 3: "☁️ Couvert",
        45: "🌫️ Brouillard", 48: "🌫️ Brouillard givrant",
        51: "🌧️ Bruine", 61: "🌧️ Pluie", 71: "❄️ Neige",
        80: "🌧️ Averses", 95: "⛈️ Orage"
    }
    return CODES.get(code, "❓")
