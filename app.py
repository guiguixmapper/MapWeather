# --- MÉTÉO (CORRIGÉE) ---
    st.write("### 🌦️ Analyse météo sur le trajet")
    
    # On sélectionne 10 points le long du trajet pour les prévisions
    indices = np.linspace(0, len(df)-1, 10, dtype=int)
    sample_points = df.iloc[indices]
    
    @st.cache_data(ttl=3600)
    def get_weather_data(lats, lons):
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}&hourly=temperature_2m,precipitation_probability,weathercode,wind_speed_10m,wind_direction_10m&timezone=auto"
        return requests.get(url).json()

    lats_str = ",".join(sample_points['lat'].astype(str))
    lons_str = ",".join(sample_points['lon'].astype(str))
    weather_res = get_weather_data(lats_str, lons_str)

    meteo_details = []
    # Open-Meteo renvoie une liste si plusieurs latitudes, ou un seul dictionnaire si une seule lat.
    weather_list = weather_res if isinstance(weather_res, list) else [weather_res]

    for idx, (_, row) in enumerate(sample_points.iterrows()):
        try:
            data = weather_list[idx]['hourly']
            # On cherche l'index de l'heure qui correspond au passage (format YYYY-MM-DDTHH:00)
            target_hour = row['time'].replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
            
            if target_hour in data['time']:
                h_idx = data['time'].index(target_hour)
                meteo_details.append({
                    "Km": round(row['km'], 1),
                    "Heure": row['time'].strftime("%H:%M"),
                    "Temp (°C)": data['temperature_2m'][h_idx],
                    "Pluie (%)": data['precipitation_probability'][h_idx],
                    "Vent (km/h)": data['wind_speed_10m'][h_idx],
                    "Dir": data['wind_direction_10m'][h_idx],
                    "lat": row['lat'], "lon": row['lon']
                })
        except:
            continue

    df_meteo_final = pd.DataFrame(meteo_details)

    # --- AFFICHAGE GRAPHIQUE ---
    fig, ax1 = plt.subplots(figsize=(12, 4))
    ax1.fill_between(df["km"], df["alt"], color="#3b82f6", alpha=0.1)
    ax1.plot(df["km"], df["alt"], color="#3b82f6", lw=2, label="Altitude")
    ax1.set_ylabel("Altitude (m)")
    ax1.set_xlabel("Distance (km)")

    ax2 = ax1.twinx()
    if not df_meteo_final.empty:
        if "Vent" in view_option:
            ax2.plot(df_meteo_final["Km"], df_meteo_final["Vent (km/h)"], color="#ef4444", marker="o", ls="--")
            ax2.set_ylabel("Vent (km/h)", color="#ef4444")
        elif "Pluie" in view_option:
            ax2.bar(df_meteo_final["Km"], df_meteo_final["Pluie (%)"], color="#06b6d4", alpha=0.4, width=2)
            ax2.set_ylabel("Pluie (%)", color="#06b6d4")
        else:
            ax2.plot(df_meteo_final["Km"], df_meteo_final["Temp (°C)"], color="#f59e0b", marker="s", ls="-")
            ax2.set_ylabel("Température (°C)", color="#f59e0b")

    st.pyplot(fig)

    # --- CARTE AVEC MARQUEURS MÉTÉO ---
    st.write("### 📍 Carte et Checkpoints")
    m = folium.Map(location=[df['lat'].mean(), df['lon'].mean()], zoom_start=11)
    folium.PolyLine(df[['lat', 'lon']].values, color="blue", weight=3, opacity=0.7).add_to(m)

    for _, pt in df_meteo_final.iterrows():
        popup_txt = f"<b>Km {pt['Km']}</b> ({pt['Heure']})<br>🌡️ {pt['Temp (°C)']}°C<br>💨 Vent: {pt['Vent (km/h)']} km/h<br>☔ Pluie: {pt['Pluie (%)']}%"
        folium.Marker(
            location=[pt['lat'], pt['lon']],
            popup=folium.Popup(popup_txt, max_width=200),
            icon=folium.Icon(color='blue', icon='cloud')
        ).add_to(m)

    st_folium(m, width="100%", height=500, key="map_final")

    # --- TABLEAU DES COLS ---
    if ascensions:
        st.write("### ⛰️ Détail des ascensions")
        st.table(pd.DataFrame(ascensions))
