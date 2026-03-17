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

# --- CONFIGURATION PAGE ---
st.set_page_config(page_title="Vélo Météo Pro", layout="wide")

# --- FONCTIONS TECHNIQUES ---
def calculer_cap(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def categoriser_ascension(dist_m, d_plus):
    if dist_m < 300 or d_plus < 15: return None
    pente = (d_plus / dist_m) * 100
    score = (dist_m / 1000) * (pente ** 2)
    if score >= 150: return "🔴 HC/1"
    elif score >= 60: return "🟡 2ème"
    elif score >= 25: return "🟢 3ème"
    elif score >= 10: return "🔵 4ème"
    return "⚪ NC"

# --- SIDEBAR ---
st.sidebar.header("📅 Planification")
date_dep = st.sidebar.date_input("Date", value=date.today())
heure_dep = st.sidebar.time_input("Départ", value=datetime.now().time())
vitesse_plat = st.sidebar.number_input("Vitesse moy. plat (km/h)", value=25)
view_option = st.sidebar.radio("Donnée météo sur profil :", ["Vent (km/h)", "Pluie (%)", "Température (°C)"])

@st.cache_data
def get_gpx_data(file_content):
    gpx = gpxpy.parse(file_content)
    points = []
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                points.append({'lat': p.latitude, 'lon': p.longitude, 'alt': p.elevation})
    return points

# --- INTERFACE PRINCIPALE ---
st.title("🚴‍♂️ Analyse Parcours & Météo")
file = st.file_uploader("Importez votre fichier GPX", type="gpx")

if file:
    raw_points = get_gpx_data(file.getvalue())
    full_data = []
    d_tot, d_plus, d_moins, t_cumul = 0, 0, 0, 0
    start_dt = datetime.combine(date_dep, heure_dep)
    
    for i in range(len(raw_points)):
        if i > 0:
            p1, p2 = raw_points[i-1], raw_points[i]
            d = gpxpy.geo.distance_2d(p1['lat'], p1['lon'], p2['lat'], p2['lon'])
            d_tot += d
            alt_diff = (p2['alt'] - p1['alt']) if (p2['alt'] is not None and p1['alt'] is not None) else 0
            if alt_diff > 0: d_plus += alt_diff
            else: d_moins += abs(alt_diff)
            v_ms = (vitesse_plat * 1000) / 3600
            if alt_diff > 0 and d > 0: v_ms /= (1 + (alt_diff/d)*10)
            t_cumul += (d / v_ms) if v_ms > 0 else 0
            
        full_data.append({
            "km": d_tot/1000, "alt": raw_points[i]['alt'] or 0, 
            "lat": raw_points[i]['lat'], "lon": raw_points[i]['lon'],
            "time": start_dt + timedelta(seconds=t_cumul)
        })

    df = pd.DataFrame(full_data)

    # --- ANALYSE DES MONTÉES ---
    ascensions, in_climb, start_idx, pente_max = [], False, 0, 0
    for i in range(2, len(df)):
        d_loc = (df.iloc[i]['km'] - df.iloc[i-2]['km']) * 1000
        if d_loc > 0:
            p_loc = ((df.iloc[i]['alt'] - df.iloc[i-2]['alt']) / d_loc) * 100
            pente_max = max(pente_max, p_loc)
        if not in_climb:
            if df.iloc[i]['alt'] > df.iloc[i-1]['alt'] + 2:
                in_climb, start_idx, pente_max = True, i-1, 0
        else:
            alt_max_loc = df.iloc[start_idx:i+1]['alt'].max()
            idx_max_loc = df.iloc[start_idx:i+1]['alt'].idxmax()
            if df.iloc[i]['alt'] < alt_max_loc - 15 or (df.iloc[i]['km'] - df.iloc[idx_max_loc]['km']) > 1.0:
                d_asc = (df.iloc[idx_max_loc]['km'] - df.iloc[start_idx]['km']) * 1000
                h_asc = df.iloc[idx_max_loc]['alt'] - df.iloc[start_idx]['alt']
                cat = categoriser_ascension(d_asc, h_asc)
                if cat and h_asc > 20:
                    ascensions.append({
                        "Départ": f"Km {round(df.iloc[start_idx]['km'],1)}", "Catégorie": cat,
                        "Distance": f"{round(d_asc/1000, 1)} km", "Pente Moy": f"{round((h_asc/d_asc)*100,1)}%",
                        "Pente Max": f"{round(pente_max, 1)}%", "D+": f"{int(h_asc)}m"
                    })
                in_climb, pente_max = False, 0

    # --- MÉTÉO ---
    indices = np.linspace(0, len(df)-1, 10, dtype=int)
    sample_points = df.iloc[indices]
    
    @st.cache_data(ttl=3600)
    def fetch_meteo(la, lo):
        url = f"https://api.open-meteo.com/v1/forecast?latitude={la}&longitude={lo}&hourly=temperature_2m,precipitation_probability,wind_speed_10m&timezone=auto"
        return requests.get(url).json()

    weather_res = fetch_meteo(",".join(sample_points['lat'].astype(str)), ",".join(sample_points['lon'].astype(str)))
    meteo_details = []
    weather_list = weather_res if isinstance(weather_res, list) else [weather_res]

    for idx, (_, row) in enumerate(sample_points.iterrows()):
        try:
            data = weather_list[idx]['hourly']
            t_str = row['time'].replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
            if t_str in data['time']:
                h_idx = data['time'].index(t_str)
                meteo_details.append({
                    "Km": round(row['km'], 1), "Heure": row['time'].strftime("%H:%M"),
                    "Temp": data['temperature_2m'][h_idx], "Pluie": data['precipitation_probability'][h_idx],
                    "Vent": data['wind_speed_10m'][h_idx], "lat": row['lat'], "lon": row['lon']
                })
        except: continue
    df_meteo = pd.DataFrame(meteo_details)

    # --- AFFICHAGE ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Distance", f"{round(d_tot/1000, 1)} km")
    c2.metric("D+ Total", f"{int(d_plus)} m")
    c3.metric("D- Total", f"{int(d_moins)} m")
    c4.metric("Arrivée Est.", df.iloc[-1]['time'].strftime("%H:%M"))

    st.write("### ⛰️ Profil & Météo")
    fig, ax1 = plt.subplots(figsize=(12, 4))
    ax1.fill_between(df["km"], df["alt"], color="#3b82f6", alpha=0.1)
    ax1.plot(df["km"], df["alt"], color="#3b82f6", lw=2)
    ax1.set_ylabel("Altitude (m)")
    
    ax2 = ax1.twinx()
    if not df_meteo.empty:
        if "Vent" in view_option: col, lab, key = "#ef4444", "Vent (km/h)", "Vent"
        elif "Pluie" in view_option: col, lab, key = "#06b6d4", "Pluie (%)", "Pluie"
        else: col, lab, key = "#f59e0b", "Temp (°C)", "Temp"
        ax2.plot(df_meteo["Km"], df_meteo[key], color=col, marker="o", ls="--")
        ax2.set_ylabel(lab, color=col)
    st.pyplot(fig)

    col_l, col_r = st.columns([1, 1])
    with col_l:
        st.write("### ⛰️ Cols détectés")
        if ascensions: st.table(pd.DataFrame(ascensions))
        else: st.info("Pas de cols majeurs.")
    with col_r:
        st.write("### 📍 Carte")
        m = folium.Map(location=[df['lat'].mean(), df['lon'].mean()], zoom_start=11)
        folium.PolyLine(df[['lat', 'lon']].values, color="blue", weight=3).add_to(m)
        for _, pt in df_meteo.iterrows():
            folium.Marker([pt['lat'], pt['lon']], icon=folium.Icon(color='blue', icon='cloud'),
                          popup=f"Km {pt['Km']}: {pt['Temp']}°C, Vent {pt['Vent']}km/h").add_to(m)
        st_folium(m, width="100%", height=400, key="map")
