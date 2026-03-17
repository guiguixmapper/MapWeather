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

# --- CONFIGURATION ---
st.set_page_config(page_title="Vélo Météo Pro", layout="wide")

# Initialisation du session_state pour bloquer les rechargements
if 'df' not in st.session_state:
    st.session_state.df = None
if 'ascensions' not in st.session_state:
    st.session_state.ascensions = []
if 'df_m' not in st.session_state:
    st.session_state.df_m = None

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# --- SIDEBAR ---
st.sidebar.header("📅 Configuration")
date_dep = st.sidebar.date_input("Date", value=date.today())
heure_dep = st.sidebar.time_input("Départ", value=datetime.now().time())
vitesse_plat = st.sidebar.number_input("Vitesse moy. plat (km/h)", value=25)
view_option = st.sidebar.radio("Météo sur profil :", ["Vent (km/h)", "Pluie (%)", "Température (°C)"])

# --- CHARGEMENT DU FICHIER ---
file = st.file_uploader("Importez votre fichier GPX", type="gpx")

if file:
    # On ne calcule TOUT que si le fichier change ou si ce n'est pas encore fait
    if st.session_state.df is None or st.sidebar.button("Forcer le recalcul"):
        with st.spinner("Analyse du tracé et récupération météo..."):
            gpx = gpxpy.parse(file.getvalue())
            pts = []
            for track in gpx.tracks:
                for seg in track.segments:
                    for p in seg.points:
                        pts.append({'lat': p.latitude, 'lon': p.longitude, 'alt': p.elevation})
            
            # Calculs trajectoire
            full_data = []
            d_tot, d_plus, t_cumul = 0, 0, 0
            start_dt = datetime.combine(date_dep, heure_dep)
            
            for i in range(len(pts)):
                if i > 0:
                    d = haversine_distance(pts[i-1]['lat'], pts[i-1]['lon'], pts[i]['lat'], pts[i]['lon'])
                    d_tot += d
                    alt_diff = (pts[i]['alt'] - pts[i-1]['alt']) if (pts[i]['alt'] is not None and pts[i-1]['alt'] is not None) else 0
                    if alt_diff > 0: d_plus += alt_diff
                    v_ms = (vitesse_plat * 1000) / 3600
                    if alt_diff > 0 and d > 0: v_ms /= (1 + (alt_diff/d)*12)
                    t_cumul += (d / v_ms) if v_ms > 0 else 0
                
                full_data.append({
                    "km": d_tot/1000, "alt": pts[i]['alt'] or 0, 
                    "lat": pts[i]['lat'], "lon": pts[i]['lon'],
                    "time": start_dt + timedelta(seconds=t_cumul)
                })
            st.session_state.df = pd.DataFrame(full_data)
            
            # Analyse Montées
            asc = []
            in_climb, start_idx, p_max = False, 0, 0
            df_tmp = st.session_state.df
            for i in range(2, len(df_tmp)):
                d_seg = (df_tmp.iloc[i]['km'] - df_tmp.iloc[i-2]['km']) * 1000
                if d_seg > 0:
                    p_max = max(p_max, ((df_tmp.iloc[i]['alt'] - df_tmp.iloc[i-2]['alt']) / d_seg) * 100)
                if not in_climb:
                    if df_tmp.iloc[i]['alt'] > df_tmp.iloc[i-1]['alt'] + 1.5:
                        in_climb, start_idx, p_max = True, i-1, 0
                else:
                    alt_max_c = df_tmp.iloc[start_idx:i+1]['alt'].max()
                    if df_tmp.iloc[i]['alt'] < alt_max_c - 12:
                        idx_m = df_tmp.iloc[start_idx:i+1]['alt'].idxmax()
                        dist_c = (df_tmp.iloc[idx_m]['km'] - df_tmp.iloc[start_idx]['km']) * 1000
                        h_c = df_tmp.iloc[idx_m]['alt'] - df_tmp.iloc[start_idx]['alt']
                        if h_c > 20 and dist_c > 400:
                            asc.append({"Départ": f"Km {round(df_tmp.iloc[start_idx]['km'],1)}", "D+": f"{int(h_c)}m", "Pente Max": f"{round(p_max,1)}%"})
                        in_climb = False
            st.session_state.ascensions = asc

            # Météo rapide
            idx_m = np.linspace(0, len(df_tmp)-1, 10, dtype=int)
            sample = df_tmp.iloc[idx_m]
            url = f"https://api.open-meteo.com/v1/forecast?latitude={','.join(sample['lat'].astype(str))}&longitude={','.join(sample['lon'].astype(str))}&hourly=temperature_2m,precipitation_probability,wind_speed_10m&timezone=auto"
            w_data = requests.get(url).json()
            w_list = w_data if isinstance(w_data, list) else [w_data]
            m_final = []
            for idx, (_, row) in enumerate(sample.iterrows()):
                try:
                    h = row['time'].replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
                    met_idx = w_list[idx]['hourly']['time'].index(h)
                    m_final.append({"Km": round(row['km'],1), "Vent": w_list[idx]['hourly']['wind_speed_10m'][met_idx], "lat": row['lat'], "lon": row['lon']})
                except: continue
            st.session_state.df_m = pd.DataFrame(m_final)

    # --- AFFICHAGE (Utilise le session_state, donc ne saute pas au zoom) ---
    if st.session_state.df is not None:
        df = st.session_state.df
        st.write(f"### 🚴‍♂️ Trajet de {round(df['km'].max(), 1)} km")
        
        # Profil
        fig, ax1 = plt.subplots(figsize=(12, 3))
        ax1.plot(df["km"], df["alt"], color="#3b82f6")
        ax1.fill_between(df["km"], df["alt"], alpha=0.1)
        st.pyplot(fig)

        col1, col2 = st.columns([1, 1])
        with col1:
            st.write("#### ⛰️ Montées")
            st.table(st.session_state.ascensions)
        with col2:
            st.write("#### 📍 Carte (Fixe)")
            m = folium.Map(location=[df['lat'].mean(), df['lon'].mean()], zoom_start=11)
            folium.PolyLine(df[['lat', 'lon']].values, color="blue", weight=3).add_to(m)
            # On n'affiche les points météo que si la table existe
            if st.session_state.df_m is not None:
                for _, p in st.session_state.df_m.iterrows():
                    folium.CircleMarker([p['lat'], p['lon']], radius=5, color='red').add_to(m)
            
            # Utilisation de st_folium avec retour vide pour stopper le refresh
            st_folium(m, width="100%", height=400, key="map_stable", returned_objects=[])
