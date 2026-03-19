# --- FICHIER : overpass.py ---

import requests
import logging
import streamlit as st
import math
import time
import copy

logger = logging.getLogger(__name__)

def distance_haversine(lat1, lon1, lat2, lon2):
    R = 6371000 # Rayon terre en mètres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

@st.cache_data(ttl=86400, show_spinner=False)
def enrichir_cols_v2(ascensions, coords_gpx): 
    if not ascensions: return ascensions
    lats = [lat for lat, lon in coords_gpx]
    lons = [lon for lat, lon in coords_gpx]
    s, n = min(lats) - 0.02, max(lats) + 0.02
    w, e = min(lons) - 0.02, max(lons) + 0.02

    # Radar géant : on cherche les cols, sommets, collines, villages, hameaux et lieux-dits
    query = f"""
    [out:json][timeout:30];
    (
      node["natural"~"saddle|peak|hill|ridge"]["name"]({s},{w},{n},{e});
      node["mountain_pass"]["name"]({s},{w},{n},{e});
      node["place"~"village|hamlet|locality|isolated_dwelling"]["name"]({s},{w},{n},{e});
    );
    out body;
    """
    
    # On met le serveur français en premier pour la rapidité !
    urls = [
        "https://overpass.openstreetmap.fr/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass-api.de/api/interpreter"
    ]
    
    ascensions_enrichies = [dict(a) for a in ascensions]
    data = None
    
    for url in urls:
        try:
            response = requests.post(url, data={"data": query}, timeout=15)
            if response.status_code == 200:
                data = response.json()
                break
            elif response.status_code == 429:
                time.sleep(1)
        except Exception as e:
            logger.warning(f"Serveur {url} échoué: {e}")
            continue

    if data:
        cols_osm = []
        for node in data.get("elements", []):
            nom = node.get("tags", {}).get("name")
            ele = node.get("tags", {}).get("ele")
            if nom:
                cols_osm.append({"lat": node["lat"], "lon": node["lon"], "nom": nom, "ele": ele})
        
        for asc in ascensions_enrichies:
            lat_a, lon_a = asc.get("_lat_sommet"), asc.get("_lon_sommet")
            if not lat_a or not lon_a: continue
            meilleur_nom, meilleure_dist, ele_osm = None, float('inf'), None
            for c in cols_osm:
                # Filtre rapide avant calcul lourd
                if abs(lat_a - c["lat"]) < 0.03 and abs(lon_a - c["lon"]) < 0.03:
                    dist = distance_haversine(lat_a, lon_a, c["lat"], c["lon"])
                    if dist < 2500 and dist < meilleure_dist:
                        meilleure_dist = dist
                        meilleur_nom = c["nom"]
                        ele_osm = c["ele"]
            if meilleur_nom:
                asc["Nom"] = meilleur_nom
                asc["Nom OSM alt"] = ele_osm
                
    return copy.deepcopy(ascensions_enrichies)

@st.cache_data(ttl=86400, show_spinner=False)
def recuperer_points_eau(coords_gpx):
    if not coords_gpx: return []
    lats = [lat for lat, lon in coords_gpx]
    lons = [lon for lat, lon in coords_gpx]
    
    # On prend 1 point sur 30 (c'est amplement suffisant pour scanner avec un rayon de 250m)
    pts_echantillon = coords_gpx[::30] 
    s, n = min(lats) - 0.02, max(lats) + 0.02
    w, e = min(lons) - 0.02, max(lons) + 0.02

    query = f"""
    [out:json][timeout:20];
    (
      node["amenity"="drinking_water"]({s},{w},{n},{e});
      node["amenity"="water_point"]({s},{w},{n},{e});
      node["natural"="spring"]({s},{w},{n},{e});
    );
    out body;
    """
    
    # Serveurs optimisés (France en premier)
    urls = [
        "https://overpass.openstreetmap.fr/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass-api.de/api/interpreter"
    ]
    
    points_eau_valides = []
    data = None
    
    for url in urls:
        try:
            response = requests.post(url, data={"data": query}, timeout=15)
            if response.status_code == 200:
                data = response.json()
                break
            elif response.status_code == 429:
                time.sleep(1)
        except Exception as e:
            logger.warning(f"Serveur Eau {url} échoué: {e}")
            continue

    if data:
        for node in data.get("elements", []):
            lat_w, lon_w = node["lat"], node["lon"]
            
            for lat_p, lon_p in pts_echantillon:
                # OPTIMISATION : Filtre mathématique très rapide (carré d'environ 400m de côté) 
                # avant de lancer le vrai calcul GPS précis. Ça accélère la boucle à la vitesse de l'éclair !
                if abs(lat_w - lat_p) < 0.004 and abs(lon_w - lon_p) < 0.004:
                    if distance_haversine(lat_w, lon_w, lat_p, lon_p) < 250:
                        nom = node.get("tags", {}).get("name", "Point d'eau")
                        points_eau_valides.append({"lat": lat_w, "lon": lon_w, "nom": nom})
                        break # Inutile de revérifier cette fontaine
                    
    return copy.deepcopy(points_eau_valides)
