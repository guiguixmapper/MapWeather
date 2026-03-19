import requests
import logging
import streamlit as st
import math

logger = logging.getLogger(__name__)

def distance_haversine(lat1, lon1, lat2, lon2):
    R = 6371000 # Rayon terre en mètres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

@st.cache_data(ttl=86400, show_spinner=False)
def enrichir_cols(ascensions, points_gpx):
    if not ascensions: return ascensions
    lats = [p.latitude for p in points_gpx]
    lons = [p.longitude for p in points_gpx]
    s, n = min(lats) - 0.02, max(lats) + 0.02
    w, e = min(lons) - 0.02, max(lons) + 0.02

    query = f"""
    [out:json][timeout:25];
    node["mountain_pass"="yes"]({s},{w},{n},{e});
    out body;
    """
    url = "https://overpass-api.de/api/interpreter"
    try:
        response = requests.post(url, data={"data": query}, timeout=15)
        if response.status_code == 200:
            data = response.json()
            cols_osm = []
            for node in data.get("elements", []):
                nom = node.get("tags", {}).get("name")
                ele = node.get("tags", {}).get("ele")
                if nom:
                    cols_osm.append({"lat": node["lat"], "lon": node["lon"], "nom": nom, "ele": ele})
            
            # Association
            for asc in ascensions:
                lat_a, lon_a = asc.get("_lat_sommet"), asc.get("_lon_sommet")
                if not lat_a or not lon_a: continue
                meilleur_nom, meilleure_dist, ele_osm = None, float('inf'), None
                for c in cols_osm:
                    dist = distance_haversine(lat_a, lon_a, c["lat"], c["lon"])
                    if dist < 1500 and dist < meilleure_dist:
                        meilleure_dist = dist
                        meilleur_nom = c["nom"]
                        ele_osm = c["ele"]
                if meilleur_nom:
                    asc["Nom"] = meilleur_nom
                    asc["Nom OSM alt"] = ele_osm
    except Exception as e:
        logger.warning(f"Overpass (Cols) échoué: {e}")
    return ascensions

@st.cache_data(ttl=86400, show_spinner=False)
def recuperer_points_eau(points_gpx):
    """Trouve les points d'eau potable à proximité du parcours (< 150m)"""
    if not points_gpx: return []
    lats = [p.latitude for p in points_gpx]
    lons = [p.longitude for p in points_gpx]
    
    # Échantillonnage pour accélérer le calcul (1 point sur 20)
    pts_echantillon = points_gpx[::20] 

    s, n = min(lats) - 0.01, max(lats) + 0.01
    w, e = min(lons) - 0.01, max(lons) + 0.01

    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="drinking_water"]({s},{w},{n},{e});
      node["amenity"="water_point"]({s},{w},{n},{e});
      node["natural"="spring"]({s},{w},{n},{e});
    );
    out body;
    """
    url = "https://overpass-api.de/api/interpreter"
    points_eau_valides = []
    
    try:
        response = requests.post(url, data={"data": query}, timeout=15)
        if response.status_code == 200:
            data = response.json()
            for node in data.get("elements", []):
                lat_w, lon_w = node["lat"], node["lon"]
                # Vérifie si le point d'eau est à moins de 150m du tracé
                for p in pts_echantillon:
                    if distance_haversine(lat_w, lon_w, p.latitude, p.longitude) < 150:
                        nom = node.get("tags", {}).get("name", "Point d'eau")
                        points_eau_valides.append({"lat": lat_w, "lon": lon_w, "nom": nom})
                        break # Passe au point d'eau suivant
    except Exception as e:
        logger.warning(f"Overpass (Eau) échoué: {e}")
        
    return points_eau_valides
