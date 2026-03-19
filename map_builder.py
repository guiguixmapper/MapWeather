# --- FICHIER : map_builder.py ---

import folium

def creer_carte(points_gpx, resultats, ascensions, points_eau, tiles="CartoDB positron", attr=None):
    kwargs = dict(location=[points_gpx[0].latitude, points_gpx[0].longitude],
                  zoom_start=11, tiles=tiles, scrollWheelZoom=True)
    if attr: kwargs["attr"] = attr
    carte = folium.Map(**kwargs)
    
    fg_meteo = folium.FeatureGroup(name="🌤️ Météo",      show=True)
    fg_cols  = folium.FeatureGroup(name="🏔️ Ascensions", show=True)
    fg_eau   = folium.FeatureGroup(name="💧 Points d'eau", show=True)
    fg_trace = folium.FeatureGroup(name="📍 Parcours",   show=True)
    
    # 1. La ligne du parcours
    folium.PolyLine([[p.latitude, p.longitude] for p in points_gpx],
                    color="#2563eb", weight=4, opacity=0.8).add_to(fg_trace)
                    
    # Fonction de style pour créer de belles pastilles HTML au lieu des gros marqueurs
    def html_icon(icone, couleur_bg):
        return f"""
        <div style="background-color:{couleur_bg}; color:white; border-radius:50%; 
                    width:26px; height:26px; display:flex; align-items:center; 
                    justify-content:center; font-size:14px; border:2px solid white; 
                    box-shadow:0 2px 4px rgba(0,0,0,0.3); line-height:1;">
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
            folium.Marker([eau["lat"], eau["lon"]], tooltip=f"💧 {eau['nom']}",
                          icon=folium.DivIcon(html=html_icon("💧", "#0ea5e9"))).add_to(fg_eau)

    # 3. Ascensions (Popups détaillés)
    COULEUR_COL = {"🔴 HC":"#dc2626","🟠 1ère Cat.":"#f97316","🟡 2ème Cat.":"#eab308","🟢 3ème Cat.":"#22c55e","🔵 4ème Cat.":"#3b82f6"}
    for asc in ascensions:
        lat_s = asc.get("_lat_sommet")
        lon_s = asc.get("_lon_sommet")
        if lat_s is None or lon_s is None: continue
        
        coul = COULEUR_COL.get(asc["Catégorie"], "#3b82f6")
        nom = asc.get("Nom", "—")
        alt_osm = asc.get("Nom OSM alt")
        
        # Le contenu texte au clic
        alt_line = (f'<div>⛰️ Sommet GPX : {asc["Alt. sommet"]}' + (f' &nbsp;·&nbsp; OSM : {alt_osm} m' if alt_osm else '') + '</div>')
        popup_col = (
            '<div style="font-family:sans-serif;font-size:12px;min-width:180px">'
            f'<div style="font-weight:700;font-size:14px;margin-bottom:6px">'
            f'{nom+" — " if nom != "—" else ""}{asc["Catégorie"]}</div>'
            f'<div>📏 {asc["Longueur"]} &nbsp;·&nbsp; D+ {asc["Dénivelé"]}</div>'
            f'<div>📐 {asc["Pente moy."]} moy. &nbsp;·&nbsp; {asc["Pente max"]} max</div>'
            + alt_line + '</div>')
            
        folium.Marker([lat_s, lon_s],
            popup=folium.Popup(popup_col, max_width=260),
            tooltip=folium.Tooltip(f'▲ {nom if nom != "—" else asc["Catégorie"]} — {asc["Alt. sommet"]}', sticky=True),
            icon=folium.DivIcon(html=html_icon("🏔️", coul))).add_to(fg_cols)
            
    # 4. Météo (Bulle affichant directement la température sur la carte)
    for cp in resultats:
        t = cp.get("temp_val")
        if t is None: continue
        
        coul_temp = "#8b5cf6" if t<5 else "#3b82f6" if t<15 else "#22c55e" if t<22 else "#f97316" if t<30 else "#ef4444"
        icon_meteo_html = f"""
        <div style="background-color:{coul_temp}; color:white; border-radius:12px; 
                    padding:3px 6px; font-size:11px; font-weight:bold; border:1px solid white; 
                    box-shadow:0 1px 3px rgba(0,0,0,0.3); text-align:center; line-height:1;">
            {t}°
        </div>"""
        
        vv = cp.get("vent_val", 0) or 0
        pp = cp.get("pluie_pct", 0) or 0
        
        # Popup météo
        popup = (f'<div style="font-family:sans-serif;font-size:12px;min-width:150px">'
                 f'<b>{cp["Heure"]} — Km {cp["Km"]}</b><br>{cp["Ciel"]} <b>{t}°C</b><br>'
                 f'💨 Vent {vv} km/h {cp["Dir"]}<br>☔ Pluie {pp}%</div>')
                 
        folium.Marker([cp["lat"], cp["lon"]],
            popup=folium.Popup(popup, max_width=200),
            tooltip=f"{cp['Heure']} | {cp['Ciel']} | 💨 {vv} km/h",
            icon=folium.DivIcon(html=icon_meteo_html)).add_to(fg_meteo)

    fg_trace.add_to(carte)
    fg_eau.add_to(carte)
    fg_cols.add_to(carte)
    fg_meteo.add_to(carte)

    folium.LayerControl(collapsed=False, position="topright").add_to(carte)

    # Petit CSS pour que le menu des calques soit joli
    css_legende = """
    <style>
    .leaflet-control-layers { border-radius: 10px !important; border: none !important; box-shadow: 0 2px 8px rgba(0,0,0,0.15) !important; font-family: Arial, sans-serif !important; }
    </style>
    """
    carte.get_root().html.add_child(folium.Element(css_legende))

    return carte
