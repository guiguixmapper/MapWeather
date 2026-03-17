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

# --- CACHING POUR ÉVITER LES RECHARGEMENTS INTEMPESTIFS ---
@st.cache_data
def charger_gpx(file_content):
    gpx = gpxpy.parse(file_content)
    pts = []
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                pts.append({'lat': p.latitude, 'lon': p.longitude, 'alt': p.elevation})
    return pts

@st.cache_data
def recuperer_meteo_batch(lats_str, lons_str):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lats_str}&longitude={lons_str}&hourly=temperature_2m,precipitation_probability,weathercode,wind_speed_10m,wind_direction_10m&timezone=auto"
    try:
        res = requests.get(url).json()
        return res if isinstance(res, list) else [res]
    except:
        return None

# --- LOGIQUE DÉTECTION DES MONTÉES AFFINÉE ---
def detecter_ascensions_precises(df, seuil_d_plus=20, fenetre_lissage=5):
    # Lissage de l'altitude pour éviter les sauts du GPS
    df['alt_smooth'] = df['Altitude (m)'].rolling(window=fenetre_lissage, center=True).mean().fillna(df['Altitude (m)'])
    
    ascensions = []
    en_montee = False
    idx_debut = 0
    
    for i in range(1, len(df)):
        alt_actuelle = df.iloc[i]['alt_smooth']
        alt_precedente = df.iloc[i-1]['alt_smooth']
        
        # Si on détecte un début de montée significatif
        if not en_montee and alt_actuelle > alt_precedente + 0.5:
            en_montee = True
            idx_debut = i-1
            
        if en_montee:
            # On cherche le point le plus haut après le début
            # Si ça descend trop longtemps (ex: plus de 1km ou perte de 20m par rapport au max local)
            alt_max_locale = df.iloc[idx_debut:i+1]['alt_smooth'].max()
            dist_depuis_max = df.iloc[i]['Distance (km)'] - df.iloc[df.iloc[idx_debut:i+1]['alt_smooth'].idxmax()]['Distance (km)']
            
            if alt_actuelle < alt_max_locale - 25 or dist_depuis_max > 1.5:
                idx_sommet = df.iloc[idx_debut:i+1]['alt_smooth'].idxmax()
                d_plus = df.iloc[idx_sommet]['alt_smooth'] - df.iloc[idx_debut]['alt_smooth']
                dist_km = df.iloc[idx_sommet]['Distance (km)'] - df.iloc[idx_debut]['Distance (km)']
                
                cat = categoriser_ascension(dist_km * 1000, d_plus)
                if cat:
                    ascensions.append({
                        "Départ": f"Km {round(df.iloc[idx_debut]['Distance (km)'], 1)}",
                        "Catégorie": cat,
                        "Distance": f"{round(dist_km, 1)} km",
                        "Pente Moy.": f"{round((d_plus/(dist_km*1000))*100, 1)}%",
                        "D+": f"{int(d_plus)}m"
                    })
                en_montee = False
                idx_debut = i
    return ascensions

def categoriser_ascension(dist_m, d_plus):
    if dist_m < 400 or d_plus < 15: return None # Plus sensible
    pente = (d_plus / dist_m) * 100
    score = (dist_m / 1000) * (pente ** 2)
    if score >= 150: return "🔴 HC/1"
    elif score >= 60: return "🟡 2ème"
    elif score >= 20: return "🟢 3ème"
    elif score >= 8: return "🔵 4ème"
    return None

def direction_vent_relative(cap_velo, dir_vent):
    diff = (dir_vent - cap_velo) % 360
    if diff <= 45 or diff >= 315: return "⬇️ Face"
    elif 135 <= diff <= 225: return "⬆️ Dos"
    else: return "💨 Côté"

# --- INTERFACE ---
st.set_page_config(page_title="Vélo Météo Pro", layout="wide")
st.sidebar.header("Configuration")

# Sélecteur de donnée sur le graphique
data_meteo_view = st.sidebar.radio("Afficher sur le profil :", ["Vent (km/h)", "Probabilité Pluie (%)", "Température (°C)"])

file = st.sidebar.file_uploader("Fichier GPX", type="gpx")

if file:
    points = charger_gpx(file.getvalue())
    if points:
        # Calcul distance/temps
        df_pts = []
        d_tot, d_plus, d_moins = 0, 0, 0
        v_moy = st.sidebar.slider("Vitesse (km/h)", 10, 40, 25)
        
        for i in range(len(points)):
            if i > 0:
                p1, p2 = points[i-1], points[i]
                d = math.sqrt((p2['lat']-p1['lat'])**2 + (p2['lon']-p1['lon'])**2) * 111320 # Approx
                d_tot += d
                diff_alt = p2['alt'] - p1['alt']
                if diff_alt > 0: d_plus += diff_alt
                else: d_moins += abs(diff_alt)
            df_pts.append({"Distance (km)": d_tot/1000, "Altitude (m)": points[i]['alt'], "lat": points[i]['lat'], "lon": points[i]['lon']})
        
        df_profil = pd.DataFrame(df_pts)
        
        # Checkpoints météo
        nb_points_meteo = 15
        indices = np.linspace(0, len(df_profil)-1, nb_points_meteo, dtype=int)
        lats_s = ",".join([str(df_profil.iloc[i]['lat']) for i in indices])
        lons_s = ",".join([str(df_profil.iloc[i]['lon']) for i in indices])
        meteo_data = recuperer_meteo_batch(lats_s, lons_s)
        
        # Traitement météo
        checkpoints = []
        if meteo_data:
            for idx, i_df in enumerate(indices):
                d_api = meteo_data[idx]['hourly']
                checkpoints.append({
                    "Km": round(df_profil.iloc[i_df]['Distance (km)'], 1),
                    "Vent": d_api['wind_speed_10m'][0],
                    "Pluie": d_api['precipitation_probability'][0],
                    "Temp": d_api['temperature_2m'][0],
                    "lat": df_profil.iloc[i_df]['lat'], "lon": df_profil.iloc[i_df]['lon']
                })

        # --- AFFICHAGE ---
        c1, c2, c3 = st.columns(3)
        c1.metric("Distance", f"{round(d_tot/1000,1)} km")
        c2.metric("D+ Total", f"{int(d_plus)} m")
        c3.metric("D- Total", f"{int(d_moins)} m")

        # Graphique Combiné
        st.subheader(f"Altitude et {data_meteo_view}")
        fig, ax1 = plt.subplots(figsize=(12, 4))
        ax1.fill_between(df_profil["Distance (km)"], df_profil["Altitude (m)"], color="gray", alpha=0.2)
        ax1.plot(df_profil["Distance (km)"], df_profil["Altitude (m)"], color="#3b82f6", lw=2)
        ax1.set_ylabel("Altitude (m)")
        
        ax2 = ax1.twinx()
        km_m = [c['Km'] for c in checkpoints]
        if "Vent" in data_meteo_view:
            vals = [c['Vent'] for c in checkpoints]; color="#ef4444"
        elif "Pluie" in data_meteo_view:
            vals = [c['Pluie'] for c in checkpoints]; color="#06b6d4"
        else:
            vals = [c['Temp'] for c in checkpoints]; color="#f59e0b"
            
        ax2.plot(km_m, vals, color=color, marker="o", ls="--", label=data_meteo_view)
        ax2.set_ylabel(data_meteo_view, color=color)
        st.pyplot(fig)

        # Montées
        st.subheader("⛰️ Montées et Cols")
        asc = detecter_ascensions_precises(df_profil)
        if asc: st.table(pd.DataFrame(asc))
        else: st.write("Aucune difficulté majeure détectée avec les réglages actuels.")

        # Carte (avec KEY pour éviter le refresh au zoom)
        st.subheader("📍 Carte du parcours")
        m = folium.Map(location=[df_profil.iloc[0]['lat'], df_profil.iloc[0]['lon']], zoom_start=11)
        folium.PolyLine(df_profil[['lat', 'lon']].values, color="blue", weight=3).add_to(m)
        st_folium(m, width="100%", height=500, key="velo_map")
