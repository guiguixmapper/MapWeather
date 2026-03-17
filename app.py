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

# --- 1. CONFIGURATION & SESSION STATE ---
st.set_page_config(page_title="Vélo Météo Pro", layout="wide")

# Initialisation des variables de stockage pour éviter les calculs à chaque zoom
if 'data' not in st.session_state:
    st.session_state.data = None # Pour le DataFrame principal
if 'climbs' not in st.session_state:
    st.session_state.climbs = [] # Pour les cols
if 'weather' not in st.session_state:
    st.session_state.weather = None # Pour la météo

# --- 2. FONCTIONS TECHNIQUES ---
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def get_cat(dist_m, d_plus):
    if dist_m < 300 or d_plus < 15: return None
    pente = (d_plus / dist_m) * 100
    score = (dist_m / 1000) * (pente ** 2)
    if score >= 150: return "🔴 HC/1"
    elif score >= 60: return "🟡 2ème"
    elif score >= 25: return "🟢 3ème"
    elif score >= 10: return "🔵 4ème"
    return "⚪ NC"

# --- 3. SIDEBAR ---
st.sidebar.header("📅 Paramètres")
date_dep = st.sidebar.date_input("Date", value=date.today())
heure_dep = st.sidebar.time_input("Départ", value=datetime.now().time())
vitesse_plat = st.sidebar.number_input("Vitesse moy. plat (km/h)", value=25)
option_y2 = st.sidebar.selectbox("Axe Y2 (Profil) :", ["Vent (km/h)", "Pluie (%)", "Température (°C)"])

btn_recalcul = st.sidebar.button("🔄 Recalculer (Météo/Vitesse)")

# --- 4. LOGIQUE DE CALCUL ---
file = st.file_uploader("Importez votre fichier GPX", type="gpx")

if file:
    # On calcule si : pas de données OU clic sur recalcul
    if st.session_state.data is None or btn_recalcul:
        with st.spinner("Analyse approfondie du parcours..."):
            gpx = gpxpy.parse(file.getvalue())
            pts = []
            for track in gpx.tracks:
                for seg in track.segments:
                    for p in seg.points:
                        pts.append({'lat': p.latitude, 'lon': p.longitude, 'alt': p.elevation})
            
            # Trajectoire et Temps
            full_pts = []
            d_tot, d_plus, d_moins, t_cumul = 0, 0, 0, 0
            start_dt = datetime.combine(date_dep, heure_dep)
            
            for i in range(len(pts)):
                if i > 0:
                    d = haversine_distance(pts[i-1]['lat'], pts[i-1]['lon'], pts[i]['lat'], pts[i]['lon'])
                    d_tot += d
                    alt_diff = (pts[i]['alt'] - pts[i-1]['alt']) if (pts[i]['alt'] is not None and pts[i-1]['alt'] is not None) else 0
                    if alt_diff > 0: d_plus += alt_diff
                    else: d_moins += abs(alt_diff)
                    v_ms = (vitesse_plat * 1000) / 3600
                    if alt_diff > 0 and d > 0: v_ms /= (1 + (alt_diff/d)*12)
                    t_cumul += (d / v_ms) if v_ms > 0 else 0
                
                full_pts.append({
                    "km": d_tot/1000, "alt": pts[i]['alt'] or 0, 
                    "lat": pts[i]['lat'], "lon": pts[i]['lon'],
                    "time": start_dt + timedelta(seconds=t_cumul)
                })
            df = pd.DataFrame(full_pts)
            st.session_state.data = df
            
            # Montées avec Pente Max
            asc = []
            in_climb, start_idx, p_max = False, 0, 0
            for i in range(2, len(df)):
                d_seg = (df.iloc[i]['km'] - df.iloc[i-2]['km']) * 1000
                if d_seg > 0:
                    p_max = max(p_max, ((df.iloc[i]['alt'] - df.iloc[i-2]['alt']) / d_seg) * 100)
                if not in_climb:
                    if df.iloc[i]['alt'] > df.iloc[i-1]['alt'] + 1.5:
                        in_climb, start_idx, p_max = True, i-1, 0
                else:
                    alt_max_c = df.iloc[start_idx:i+1]['alt'].max()
                    idx_max_c = df.iloc[start_idx:i+1]['alt'].idxmax()
                    if df.iloc[i]['alt'] < alt_max_c - 12 or (df.iloc[i]['km'] - df.iloc[idx_max_c]['km']) > 0.8:
                        dist_c = (df.iloc[idx_max_c]['km'] - df.iloc[start_idx]['km']) * 1000
                        h_c = df.iloc[idx_max_c]['alt'] - df.iloc[start_idx]['alt']
                        cat = get_cat(dist_c, h_c)
                        if cat and h_c > 15:
                            asc.append({"Départ": f"Km {round(df.iloc[start_idx]['km'],1)}", "Catégorie": cat, "Distance": f"{round(dist_c/1000,1)}km", "Pente Moy": f"{round((h_c/dist_c)*100,1)}%", "Pente Max": f"{round(p_max,1)}%", "D+": f"{int(h_c)}m"})
                        in_climb, p_max = False, 0
            st.session_state.climbs = asc

            # Météo détaillée
            indices = np.linspace(0, len(df)-1, 12, dtype=int)
            sample = df.iloc[indices]
            url = f"https://api.open-meteo.com/v1/forecast?latitude={','.join(sample['lat'].astype(str))}&longitude={','.join(sample['lon'].astype(str))}&hourly=temperature_2m,precipitation_probability,wind_speed_10m&timezone=auto"
            res = requests.get(url).json()
            res_list = res if isinstance(res, list) else [res]
            m_final = []
            for idx, (_, row) in enumerate(sample.iterrows()):
                try:
                    target_h = row['time'].replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
                    met_idx = res_list[idx]['hourly']['time'].index(target_h)
                    m_final.append({
                        "Km": round(row['km'], 1), "Heure": row['time'].strftime("%H:%M"),
                        "Vent": res_list[idx]['hourly']['wind_speed_10m'][met_idx],
                        "Pluie": res_list[idx]['hourly']['precipitation_probability'][met_idx],
                        "Temp": res_list[idx]['hourly']['temperature_2m'][met_idx],
                        "lat": row['lat'], "lon": row['lon']
                    })
                except: continue
            st.session_state.weather = pd.DataFrame(m_final)

    # --- 5. AFFICHAGE (STABLE AU ZOOM) ---
    if st.session_state.data is not None:
        df = st.session_state.data
        df_m = st.session_state.weather
        
        st.subheader("📊 Résumé du parcours")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Distance", f"{round(df['km'].max(),1)} km")
        c2.metric("D+ Total", f"{int(df['alt'].diff().clip(lower=0).sum())} m")
        c3.metric("Arrivée Est.", df.iloc[-1]['time'].strftime("%H:%M"))
        c4.metric("Points Météo", len(df_m))

        # Profil avec Axe Y2 dynamique
        fig, ax1 = plt.subplots(figsize=(12, 4))
        ax1.fill_between(df["km"], df["alt"], color="#3b82f6", alpha=0.1)
        ax1.plot(df["km"], df["alt"], color="#3b82f6", lw=2, label="Altitude")
        ax1.set_ylabel("Altitude (m)")
        
        ax2 = ax1.twinx()
        if "Vent" in option_y2: col, key, lab = "#ef4444", "Vent", "Vent (km/h)"
        elif "Pluie" in option_y2: col, key, lab = "#06b6d4", "Pluie", "Pluie (%)"
        else: col, key, lab = "#f59e0b", "Temp", "Temp (°C)"
        
        ax2.plot(df_m["Km"], df_m[key], color=col, marker="o", ls="--", label=lab)
        ax2.set_ylabel(lab, color=col)
        st.pyplot(fig)

        col_l, col_r = st.columns([1, 1.2])
        with col_l:
            st.write("### ⛰️ Cols et Difficultés")
            if st.session_state.climbs: st.table(pd.DataFrame(st.session_state.climbs))
            else: st.info("Aucune montée répertoriée.")

        with col_r:
            st.write("### 📍 Carte interactive (Stable)")
            m = folium.Map(location=[df['lat'].mean(), df['lon'].mean()], zoom_start=11)
            folium.PolyLine(df[['lat', 'lon']].values, color="blue", weight=3).add_to(m)
            for _, p in df_m.iterrows():
                txt = f"Km {p['Km']} ({p['Heure']}): {p['Temp']}°C, Vent {p['Vent']}km/h"
                folium.Marker([p['lat'], p['lon']], popup=txt, icon=folium.Icon(color='blue', icon='info-sign')).add_to(m)
            
            # Fix ultime : returned_objects=[] et session_state
            st_folium(m, width="100%", height=450, key="map_stable", returned_objects=[])
