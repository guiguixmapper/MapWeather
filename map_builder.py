import folium

def creer_carte_moderne(points_gpx, resultats, ascensions, points_eau, tiles="CartoDB positron", attr=None):
    kwargs = dict(location=[points_gpx[0].latitude, points_gpx[0].longitude],
                  zoom_start=11, tiles=tiles, scrollWheelZoom=True)
    if attr: kwargs["attr"] = attr
    carte = folium.Map(**kwargs)
    
    fg_trace = folium.FeatureGroup(name="📍 Parcours", show=True)
    fg_cols  = folium.FeatureGroup(name="🏔️ Ascensions", show=True)
    fg_meteo = folium.FeatureGroup(name="🌤️ Météo", show=True)
    fg_eau   = folium.FeatureGroup(name="💧 Points d'eau", show=True)
    
    # 1. Tracé
    folium.PolyLine([[p.latitude, p.longitude] for p in points_gpx],
                    color="#2563eb", weight=4, opacity=0.8).add_to(fg_trace)
                    
    # Style de pastille générique
    def html_icon(icone, couleur_bg):
        return f"""
        <div style="background-color:{couleur_bg}; color:white; border-radius:50%; 
                    width:24px; height:24px; display:flex; align-items:center; 
                    justify-content:center; font-size:12px; border:2px solid white; 
                    box-shadow:0 2px 4px rgba(0,0,0,0.3);">
            {icone}
        </div>"""

    # Départ / Arrivée
    folium.Marker([points_gpx[0].latitude, points_gpx[0].longitude], tooltip="Départ",
                  icon=folium.DivIcon(html=html_icon("🟢", "#10b981"))).add_to(fg_trace)
    folium.Marker([points_gpx[-1].latitude, points_gpx[-1].longitude], tooltip="Arrivée",
                  icon=folium.DivIcon(html=html_icon("🏁", "#ef4444"))).add_to(fg_trace)

    # 2. Points d'eau
    if points_eau:
        for eau in points_eau:
            folium.Marker([eau["lat"], eau["lon"]], tooltip=eau["nom"],
                          icon=folium.DivIcon(html=html_icon("💧", "#0ea5e9"))).add_to(fg_eau)

    # 3. Ascensions
    COULEUR_COL = {"🔴 HC":"#dc2626","🟠 1ère Cat.":"#f97316","🟡 2ème Cat.":"#eab308","🟢 3ème Cat.":"#22c55e","🔵 4ème Cat.":"#3b82f6"}
    for asc in ascensions:
        if not asc.get("_lat_sommet"): continue
        coul = COULEUR_COL.get(asc["Catégorie"], "#3b82f6")
        nom = asc.get("Nom", "")
        titre = f"▲ {nom} {asc['Catégorie']}" if nom != "—" else f"▲ {asc['Catégorie']}"
        folium.Marker([asc["_lat_sommet"], asc["_lon_sommet"]], tooltip=titre,
                      icon=folium.DivIcon(html=html_icon("🏔️", coul))).add_to(fg_cols)

    # 4. Météo (avec Température sur la carte)
    for cp in resultats:
        t = cp.get("temp_val")
        if t is None: continue
        
        coul_temp = "#8b5cf6" if t<5 else "#3b82f6" if t<15 else "#22c55e" if t<22 else "#f97316" if t<30 else "#ef4444"
        icon_meteo_html = f"""
        <div style="background-color:{coul_temp}; color:white; border-radius:12px; 
                    padding:2px 6px; font-size:10px; font-weight:bold; border:1px solid white; 
                    box-shadow:0 1px 3px rgba(0,0,0,0.3); text-align:center;">
            {t}°
        </div>"""
        
        tt = f"{cp['Heure']} | {cp['Ciel']} | Vent {cp.get('vent_val','-')}km/h {cp.get('Dir','')}"
        folium.Marker([cp["lat"], cp["lon"]], tooltip=tt,
                      icon=folium.DivIcon(html=icon_meteo_html)).add_to(fg_meteo)

    fg_trace.add_to(carte)
    fg_eau.add_to(carte)
    fg_cols.add_to(carte)
    fg_meteo.add_to(carte)
    
    folium.LayerControl(collapsed=False).add_to(carte)
    return carte
