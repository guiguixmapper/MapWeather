import streamlit as st
import pandas as pd
import gpxpy
import folium
from streamlit_folium import st_folium
import requests
from datetime import datetime, timedelta, date
import matplotlib.pyplot as plt
import math
import numpy as np

# --- FONCTIONS MATHÉMATIQUES ET TRADUCTEURS ---
def calculer_cap(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    cap_initial = math.atan2(x, y)
    return (math.degrees(cap_initial) + 360) % 360

def direction_vent_relative(cap_velo, dir_vent):
    diff = (dir_vent - cap_velo) % 360
    if diff <= 45 or diff >= 315: return "⬇️ Face"
    elif 135 <= diff <= 225: return "⬆️ Dos"
    elif 45 < diff < 135: return "↘️ Côté (D)"
    else: return "↙️ Côté (G)"

def categoriser_ascension(distance_m, d_plus):
    if distance_m < 500 or d_plus < 30: return None
    pente_moyenne = (d_plus / distance_m) * 100
    if pente_moyenne < 1.5: return None 
    
    score = (distance_m / 1000) * (pente_moyenne ** 2)
    if score >= 250: return "🔴 HC"
    elif score >= 150: return "🟠 1ère Cat."
    elif score >= 80: return "🟡 2ème Cat."
    elif score >= 35: return "🟢 3ème Cat."
    elif score >= 15: return "🔵 4ème Cat."
    else: return "⚪ NC"

def obtenir_icone_meteo(code):
    mapping = {0: "☀️ Clair", 1: "⛅ Éclaircies", 2: "⛅ Éclaircies", 3: "☁️ Couvert", 
               45: "🌫️ Brouillard", 48: "🌫️ Brouillard", 51: "🌦️ Bruine", 61: "🌧️ Pluie", 
               71: "❄️ Neige", 95: "⛈️ Orage"}
    return mapping.get(code, "❓ Inconnu")

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Vélo & Météo Pro", layout="wide")
st.title("🚴‍♂️ Mon Parcours Vélo & Météo")

st.sidebar.header("⚙️ Paramètres")
date_depart_choisie = st.sidebar.date_input("Date de départ", value=date.today())
heure_depart = st.sidebar.time_input("Heure de départ")
vitesse_moyenne = st.sidebar.number_input("Vitesse moy. plat (km/h)", value=25)

intervalle_min = st.sidebar.selectbox("Intervalle météo", options=[5, 10, 15], index=1)
intervalle_sec = intervalle_min * 60

# --- 2. IMPORT GPX ---
fichier_gpx = st.file_uploader("Importez votre tracé (.gpx)", type=["gpx"])

if fichier_gpx:
    gpx = gpxpy.parse(fichier_gpx)
    points_gpx = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points_gpx.append(point)

    if points_gpx:
        # --- CALCULS TRAJET ---
        checkpoints = []
        profil_data = []
        dist_totale_m, d_plus_total, d_moins_total, temps_total_sec = 0, 0, 0, 0
        prochain_checkpoint_sec = 0
        date_depart = datetime.combine(date_depart_choisie, heure_depart)

        for i in range(1, len(points_gpx)):
            p1, p2 = points_gpx[i-1], points_gpx[i]
            dist = p1.distance_2d(p2) or 0
            
            # Ajustement vitesse selon pente
            d_plus_local = max(0, (p2.elevation - p1.elevation)) if p2.elevation and p1.elevation else 0
            d_plus_total += d_plus_local
            if p2.elevation and p1.elevation and p2.elevation < p1.elevation:
                d_moins_total += abs(p2.elevation - p1.elevation)

            dist_ajustee = dist + (d_plus_local * 10)
            vitesse_ms = (vitesse_moyenne * 1000) / 3600
            temps_sec = dist_ajustee / vitesse_ms if vitesse_ms > 0 else 0

            dist_totale_m += dist
            temps_total_sec += temps_sec
            
            profil_data.append({"Distance (km)": dist_totale_m / 1000, "Altitude (m)": p2.elevation})

            if temps_total_sec >= prochain_checkpoint_sec:
                heure_p = date_depart + timedelta(seconds=temps_total_sec)
                checkpoints.append({
                    "lat": p2.latitude, "lon": p2.longitude, 
                    "Cap": calculer_cap(p1.latitude, p1.longitude, p2.latitude, p2.longitude),
                    "Heure": heure_p.strftime("%H:%M"),
                    "Heure_API": heure_p.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
                    "Km": round(dist_totale_m / 1000, 1)
                })
                prochain_checkpoint_sec += intervalle_sec

        df_profil = pd.DataFrame(profil_data)

        # --- DÉTECTION DES COLS (AMÉLIORÉE) ---
        ascensions = []
        en_montee = False
        debut_idx, idx_max, alt_max, pente_max_loc = 0, 0, 0, 0

        for i in range(1, len(df_profil)):
            alt, dist = df_profil.iloc[i]['Altitude (m)'], df_profil.iloc[i]['Distance (km)']
            if not en_montee:
                if alt > df_profil.iloc[debut_idx]['Altitude (m)'] + 15:
                    en_montee, idx_max, alt_max, pente_max_loc = True, i, alt, 0
            else:
                # Calcul pente max sur 100m
                for j in range(i-1, 0, -1):
                    d_diff = dist - df_profil.iloc[j]['Distance (km)']
                    if d_diff >= 0.1:
                        pente = ((alt - df_profil.iloc[j]['Altitude (m)']) / (d_diff * 1000)) * 100
                        pente_max_loc = max(pente_max_loc, pente)
                        break
                
                if alt > alt_max: 
                    alt_max, idx_max = alt, i
                # Condition de fin : descente de 40m ET au moins 500m après le sommet (évite de couper sur replat)
                elif alt < alt_max - 40 and (dist - df_profil.iloc[idx_max]['Distance (km)']) > 0.5:
                    d_deb = df_profil.iloc[debut_idx]['Distance (km)']
                    dist_col = df_profil.iloc[idx_max]['Distance (km)'] - d_deb
                    d_p = alt_max - df_profil.iloc[debut_idx]['Altitude (m)']
                    cat = categoriser_ascension(dist_col * 1000, d_p)
                    if cat:
                        ascensions.append({"Départ": f"Km {round(d_deb, 1)}", "Catégorie": cat, "Distance": f"{round(dist_col, 1)} km", "Pente Moy.": f"{round((d_p/(dist_col*1000))*100, 1)}%", "Pente Max": f"{round(pente_max_loc, 1)}%", "D+": f"{int(d_p)}m"})
                    en_montee, debut_idx = False, i

        # --- MÉTÉO ---
        lats, lons = ",".join([str(c['lat']) for c in checkpoints]), ",".join([str(c['lon']) for c in checkpoints])
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}&hourly=temperature_2m,precipitation_probability,weathercode,wind_speed_10m,wind_direction_10m&timezone=auto"
        
        try:
            res = requests.get(url).json()
            res_list = res if isinstance(res, list) else [res]
            for i, cp in enumerate(checkpoints):
                data = res_list[i]['hourly']
                if cp['Heure_API'] in data['time']:
                    idx = data['time'].index(cp['Heure_API'])
                    cp.update({
                        "Ciel": obtenir_icone_meteo(data['weathercode'][idx]),
                        "Temp": f"{data['temperature_2m'][idx]}°",
                        "Pluie": data['precipitation_probability'][idx],
                        "Vent": data['wind_speed_10m'][idx],
                        "Dir": data['wind_direction_10m'][idx],
                        "Effet Vent": direction_vent_relative(cp["Cap"], data['wind_direction_10m'][idx])
                    })
        except: st.error("Erreur météo")

        # --- AFFICHAGE ---
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Distance", f"{round(dist_totale_m/1000, 1)} km")
        col2.metric("Dénivelé +", f"{int(d_plus_total)} m")
        col3.metric("Dénivelé -", f"{int(d_moins_total)} m")
        col4.metric("Arrivée Est.", (date_depart + timedelta(seconds=temps_total_sec)).strftime("%H:%M"))

        # --- GRAPHIQUE COMBINÉ ---
        st.write("### ⛰️ Profil & Conditions (Altitude + Vent/Pluie)")
        fig, ax1 = plt.subplots(figsize=(12, 4))
        ax1.fill_between(df_profil["Distance (km)"], df_profil["Altitude (m)"], color="#3b82f6", alpha=0.1)
        ax1.plot(df_profil["Distance (km)"], df_profil["Altitude (m)"], color="#3b82f6", lw=2, label="Altitude")
        ax1.set_ylabel("Altitude (m)", color="#3b82f6")
        
        ax2 = ax1.twinx()
        cp_km = [c['Km'] for c in checkpoints if 'Vent' in c]
        if cp_km:
            vent_vals = [c['Vent'] for c in checkpoints if 'Vent' in c]
            pluie_vals = [c['Pluie'] for c in checkpoints if 'Pluie' in c]
            ax2.plot(cp_km, vent_vals, color="#ef4444", ls="--", lw=1.5, label="Vent (km/h)")
            ax2.fill_between(cp_km, 0, pluie_vals, color="#06b6d4", alpha=0.1, label="Pluie %")
            ax2.set_ylabel("Vent (km/h) / Pluie (%)", color="#ef4444")
            
        fig.tight_layout()
        st.pyplot(fig)

        # --- CARTE ---
        m = folium.Map(location=[points_gpx[0].latitude, points_gpx[0].longitude], zoom_start=12)
        folium.PolyLine([[p.latitude, p.longitude] for p in points_gpx], color="blue", weight=4).add_to(m)
        for cp in checkpoints:
            if 'Temp' in cp:
                folium.Marker([cp['lat'], cp['lon']], tooltip=f"{cp['Heure']}: {cp['Temp']} - {cp['Effet Vent']}", 
                              icon=folium.Icon(color="blue", icon="info-sign")).add_to(m)
        st_folium(m, width=1000, height=400)

        # --- TABLES ---
        st.write("### ⛰️ Cols détectés")
        st.dataframe(pd.DataFrame(ascensions) if ascensions else "Aucun col détecté", use_container_width=True)
        
        st.write("### ⏱️ Détails Météo")
        st.dataframe(pd.DataFrame(checkpoints).drop(columns=['lat','lon','Heure_API','Cap','Dir']), use_container_width=True)

else:
    st.info("👋 En attente d'un fichier GPX pour analyser le parcours.")
