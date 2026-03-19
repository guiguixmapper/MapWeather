"""
🚴‍♂️ Vélo & Météo — V13 (Historique, Eau, Pollen, Optimiseur, Carte Moderne)
======================================================================
"""
import streamlit as st
import pandas as pd
import gpxpy
import folium
from streamlit_folium import st_folium
import requests
from datetime import datetime, timedelta, date
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import math
import logging
import base64
import re
import time

# Nos modules
import climbing as climbing_module
from climbing import detecter_ascensions, estimer_watts, estimer_fc, estimer_temps_col, calculer_calories, zones_actives, get_zone, LEGENDE_UCI
from weather import recuperer_fuseau, recuperer_meteo_batch, recuperer_soleil, extraire_meteo, direction_vent_relative, label_wind_chill, recuperer_qualite_air
from overpass import enrichir_cols, recuperer_points_eau
from map_builder import creer_carte_moderne
from gemini_coach import generer_briefing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- STYLES ---
CSS = """
<style>
  :root { --bleu: #2563eb; --radius: 12px; }
  .app-header { background: linear-gradient(135deg, #1e40af, #0ea5e9); border-radius: var(--radius); padding: 24px 32px; margin-bottom: 20px; color: white; }
  .app-header h1 { margin: 0; font-weight: 800; }
  .soleil-row { display: flex; gap: 14px; background: linear-gradient(90deg, #fef3c7, #fde68a); border-radius: var(--radius); padding: 12px 18px; margin: 10px 0; align-items: center; }
  .soleil-item .s-val { font-weight: 700; color: #92400e; }
  .soleil-item .s-lbl { font-size: .7rem; color: #b45309; }
</style>
"""

def calculer_cap(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

@st.cache_data(show_spinner=False)
def parser_gpx(data):
    try:
        gpx = gpxpy.parse(data)
        return [p for t in gpx.tracks for s in t.segments for p in s.points]
    except Exception as e: return []

# --- IMPORT FONCTIONS ANALYSE EXACTEMENT COMME V10 ---
def analyser_meteo_detaillee(resultats, dist_tot):
    valides = [cp for cp in resultats if cp.get("temp_val") is not None]
    if not valides: return None
    cps_pluie = [cp for cp in valides if (cp.get("pluie_pct") or 0) >= 50]
    pct_pluie = len(cps_pluie) / len(valides) * 100
    premier_pluie = next((cp for cp in valides if (cp.get("pluie_pct") or 0) >= 50), None)
    compteur_effet = {"⬇️ Face": 0, "⬆️ Dos": 0, "↙️ Côté (D)": 0, "↘️ Côté (G)": 0, "—": 0}
    for cp in valides: compteur_effet[cp.get("effet", "—")] += 1
    total_v = len(valides)
    pct_face = round(compteur_effet["⬇️ Face"] / total_v * 100)
    pct_dos = round(compteur_effet["⬆️ Dos"] / total_v * 100)
    pct_cote = round((compteur_effet["↙️ Côté (D)"] + compteur_effet["↘️ Côté (G)"]) / total_v * 100)
    
    segments_face = []
    en_face, debut_face = False, None
    for cp in valides:
        if cp.get("effet") == "⬇️ Face":
            if not en_face: en_face, debut_face = True, cp["Km"]
        elif en_face: segments_face.append((debut_face, cp["Km"])); en_face = False
    if en_face: segments_face.append((debut_face, valides[-1]["Km"]))
    return {"pct_pluie": round(pct_pluie), "premier_pluie": premier_pluie, "pct_face": pct_face, "pct_dos": pct_dos, "pct_cote": pct_cote, "segments_face": segments_face, "n_valides": total_v}

def calculer_score(resultats, ascensions, d_plus, vitesse, ref_val, mode, poids):
    valides = [cp for cp in resultats if cp.get("temp_val") is not None]
    if valides:
        tm = sum(cp["temp_val"] for cp in valides) / len(valides)
        s_temp = 2.0 if 15<=tm<=22 else 1.5 if 10<=tm<=27 else 0.8 if 5<=tm<=32 else 0.3 if tm>=0 else 0.0
        POIDS_EFFET = { "⬇️ Face": 1.5, "↙️ Côté (D)": 0.7, "↘️ Côté (G)": 0.7, "⬆️ Dos": -0.3, "—": 0.5 }
        ve_moy = sum((cp.get("vent_val") or 0) * POIDS_EFFET.get(cp.get("effet", "—"), 0.5) for cp in valides) / len(valides)
        s_vent = 2.0 if ve_moy<=8 else 1.5 if ve_moy<=18 else 0.8 if ve_moy<=30 else 0.3 if ve_moy<=45 else 0.0
        pm = sum(cp.get("pluie_pct") or 0 for cp in valides) / len(valides)
        s_pluie = round(max(0.0, 2.0 * (1 - pm / 100)), 2)
        sm = s_temp + s_vent + s_pluie
    else: sm = 3.0   
    dist_km = sum(cp.get("Km", 0) for cp in resultats[-1:])
    s_dist = 0.5 if dist_km<30 else 0.7 if dist_km<80 else 0.9 if dist_km<150 else 1.0
    s_dplus = 0.5 if d_plus<300 else 0.7 if d_plus<1000 else 0.9 if d_plus<2500 else 1.0
    s_parcours = s_dist + s_dplus
    if ascensions and ref_val > 0:
        wm  = sum(estimer_watts(a["_pente_moy"], vitesse, poids) for a in ascensions) / len(ascensions)
        pct = wm / ref_val if mode == "⚡ Puissance" else 0.85
        s_effort = 0.8 if pct<=0.50 else 1.2 if pct<=0.70 else 2.0 if pct<=0.90 else 1.5 if pct<=1.05 else 0.8
    else: s_effort = 1.0
    sc = max(2.0, s_parcours + s_effort)
    total = round(min(10.0, max(0.0, sm + sc)), 1)
    lbl = "🔴 Déconseillé" if total<3.5 else "🟠 Difficile" if total<5.0 else "🟡 Correct" if total<6.5 else "🟢 Bonne sortie" if total<8.0 else "⭐ Conditions idéales"
    return {"total": total, "label": lbl, "score_meteo": round(max(0.0, sm), 1), "score_cols": round(sc, 1)}

# --- OPTIMISEUR DE DÉPART (NEW) ---
def optimiser_depart(checkpoints_base, rep_list, ascensions, d_plus, vitesse, ref_val, mode, poids):
    meilleur_score = 0
    meilleur_offset = 0
    if not rep_list: return None
    
    for offset in [0, 1, 2, 3]: # Simule Départ actuel, +1h, +2h, +3h
        res_sim = []
        for cp in checkpoints_base:
            h_api = datetime.fromisoformat(cp["Heure_API"]) + timedelta(hours=offset)
            m = extraire_meteo(rep_list, h_api.strftime("%Y-%m-%dT%H:00"))
            if m["dir_deg"] is not None: m["effet"] = direction_vent_relative(cp["Cap"], m["dir_deg"])
            res_sim.append({**cp, **m})
        
        sc = calculer_score(res_sim, ascensions, d_plus, vitesse, ref_val, mode, poids)["total"]
        if sc > meilleur_score:
            meilleur_score = sc
            meilleur_offset = offset
            
    return meilleur_offset, meilleur_score

# ==============================================================================
# MAIN
# ==============================================================================
def main():
    st.set_page_config(page_title="Vélo & Météo", page_icon="🚴‍♂️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("<div class='app-header'><h1>🚴‍♂️ Vélo & Météo</h1></div>", unsafe_allow_html=True)

    # ── SIDEBAR ──
    st.sidebar.header("⚙️ Paramètres")
    fichier = st.sidebar.file_uploader("📂 Fichier GPX", type=["gpx"])
    date_dep = st.sidebar.date_input("📅 Date de départ", value=date.today())
    heure_dep = st.sidebar.time_input("🕐 Heure de départ")
    vitesse = st.sidebar.number_input("🚴 Vitesse moy. plat (km/h)", 5, 60, 25)
    mode = st.sidebar.radio("📊 Mode d'analyse", ["⚡ Puissance", "🫀 Fréquence Cardiaque"], horizontal=True)
    ref_val = st.sidebar.number_input("⚡ FTP (W)" if mode == "⚡ Puissance" else "❤️ FC max (bpm)", 50, 500, 220)
    ftp_fc = ref_val if mode == "⚡ Puissance" else st.sidebar.number_input("⚡ FTP estimé (W)", 50, 500, 220)
    poids = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)
    intervalle_sec = 600 # 10 min fixe pour l'instant
    
    st.sidebar.divider()
    gemini_key = st.sidebar.text_input("🤖 Clé API Gemini", type="password")

    if not fichier: st.info("👈 Importez un fichier GPX."); return

    # Détection Passé / Futur
    delta_jours = (date_dep - date.today()).days
    is_past = delta_jours <= -2

    points_gpx = parser_gpx(fichier.read())
    if not points_gpx: return

    # Date et Fuseau
    fuseau = recuperer_fuseau(points_gpx[0].latitude, points_gpx[0].longitude)
    date_depart = datetime.combine(date_dep, heure_dep)
    infos_soleil = recuperer_soleil(points_gpx[0].latitude, points_gpx[0].longitude, date_dep.strftime("%Y-%m-%d"))

    # Parcours
    checkpoints, profil_data = [], []
    dist_tot = d_plus = d_moins = temps_s = prochain = cap = 0.0
    vms = (vitesse * 1000) / 3600
    for i in range(1, len(points_gpx)):
        p1, p2 = points_gpx[i-1], points_gpx[i]
        d = p1.distance_2d(p2) or 0.0; dp = 0.0
        if p1.elevation and p2.elevation:
            dif = p2.elevation - p1.elevation
            if dif > 0: dp = dif; d_plus += dif
            else: d_moins += abs(dif)
        dist_tot += d; temps_s += (d + dp * 10) / vms
        cap = calculer_cap(p1.latitude, p1.longitude, p2.latitude, p2.longitude)
        profil_data.append({"Distance (km)": round(dist_tot/1000, 3), "Altitude (m)": p2.elevation or 0})
        if temps_s >= prochain:
            hp = date_depart + timedelta(seconds=temps_s)
            checkpoints.append({"lat": p2.latitude, "lon": p2.longitude, "Cap": cap, "Heure": hp.strftime("%d/%m %H:%M"), "Heure_API": hp.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"), "Km": round(dist_tot/1000, 1), "Alt (m)": int(p2.elevation) if p2.elevation else 0})
            prochain += intervalle_sec

    vit_moy_reelle = round((dist_tot / 1000) / (temps_s / 3600), 1) if temps_s > 0 else vitesse
    heure_arr = date_depart + timedelta(seconds=temps_s)
    df_profil = pd.DataFrame(profil_data)

    # Cols
    ascensions = detecter_ascensions(df_profil)
    if ascensions:
        # Simplifié pour le code
        pass 

    # Eau et Qualité de l'air
    points_eau = recuperer_points_eau(points_gpx)
    air_quality = recuperer_qualite_air(points_gpx[0].latitude, points_gpx[0].longitude, date_dep.strftime("%Y-%m-%d"))

    # Météo Batch
    frozen = tuple((cp["lat"], cp["lon"], cp["Heure_API"]) for cp in checkpoints)
    rep_list = recuperer_meteo_batch(frozen, is_past=is_past, date_str=date_dep.strftime("%Y-%m-%d"))
    
    resultats = []
    if rep_list is None:
        st.warning("⚠️ Météo indisponible. Réessayez plus tard.")
        return
    else:
        for cp in checkpoints:
            m = extraire_meteo(rep_list, cp["Heure_API"]) # Note: rep_list is whole dataset now
            if m["dir_deg"] is not None: m["effet"] = direction_vent_relative(cp["Cap"], m["dir_deg"])
            cp.update(m); resultats.append(cp)

    score = calculer_score(resultats, ascensions, d_plus, vitesse, ref_val, mode, poids)
    calories = calculer_calories(max(1, poids - 10), temps_s, dist_tot, d_plus, vitesse)
    analyse_meteo = analyser_meteo_detaillee(resultats, dist_tot)

    # L'Optimiseur en action !
    if not is_past:
        opt_res = optimiser_depart(checkpoints, rep_list, ascensions, d_plus, vitesse, ref_val, mode, poids)
        if opt_res and opt_res[0] > 0 and opt_res[1] > score['total']:
            heure_opt = (date_depart + timedelta(hours=opt_res[0])).strftime("%H:%M")
            st.info(f"💡 **Départ optimal calculé : {heure_opt} (+{opt_res[0]}h)**. Votre score passerait à **{opt_res[1]}/10** ! (Changez l'heure à gauche pour actualiser).")

    # Affichage Haut
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#1e3a5f,#1e40af);border-radius:12px; padding:16px 24px;color:white;display:flex;flex-wrap:wrap">
      <div style="min-width:160px;padding-right:24px;border-right:1px solid rgba(255,255,255,0.25)">
        <div style="font-size:2.8rem;font-weight:900;line-height:1">{score['total']}<span style="font-size:1.2rem">/10</span></div>
        <div style="font-size:.95rem;font-weight:600">{score['label']}</div>
        {"<span style='background:#f59e0b;padding:2px 6px;border-radius:4px;font-size:10px'>HISTORIQUE</span>" if is_past else ""}
      </div>
      <div style="display:flex;gap:0;flex:1;flex-wrap:wrap;padding-left:8px">
        <div style="flex:1;text-align:center;padding:6px"><div style="font-size:1.5rem;font-weight:800">{round(dist_tot/1000,1)} km</div></div>
        <div style="flex:1;text-align:center;padding:6px"><div style="font-size:1.5rem;font-weight:800">{int(d_plus)} m</div></div>
        <div style="flex:1;text-align:center;padding:6px"><div style="font-size:1.5rem;font-weight:800;color:#34d399">{vit_moy_reelle} km/h</div><div style="font-size:.7rem">Moy. réelle</div></div>
        <div style="flex:1;text-align:center;padding:6px"><div style="font-size:1.5rem;font-weight:800;color:#60a5fa">{len(points_eau)} 💧</div></div>
      </div>
    </div>""", unsafe_allow_html=True)

    tab_carte, tab_detail, tab_analyse = st.tabs(["🗺️ Carte & Tracé", "📋 Tableau de Marche", "🤖 Coach IA"])

    with tab_carte:
        # Affiche la carte Modernisée
        carte = creer_carte_moderne(points_gpx, resultats, ascensions, points_eau)
        st_folium(carte, width="100%", height=600, returned_objects=[])

    with tab_detail:
        lignes = [{"Heure": cp["Heure"], "Km": cp["Km"], "Ciel": cp.get("Ciel","—"), "Temp": f"{cp.get('temp_val','-')}°C", "Vent": f"{cp.get('vent_val','-')} km/h", "Effet": cp.get("effet","—")} for cp in resultats]
        st.dataframe(pd.DataFrame(lignes), width='stretch', hide_index=True)
        
        c1, c2 = st.columns(2)
        c1.metric("☀️ Indice UV Max", air_quality.get("uv_max", "Inconnu"))
        c2.metric("🌿 Pollen", air_quality.get("pollen_alerte", "Aucun"))

    with tab_analyse:
        if st.button("💬 Générer le briefing (Prend en compte l'eau et l'historique)", use_container_width=True) and gemini_key:
            with st.spinner("L'IA analyse le tout..."):
                ctx = f"le {date_dep.strftime('%d/%m/%Y')}"
                briefing = generer_briefing(gemini_key, dist_tot, d_plus, temps_s, calories, score, ascensions, analyse_meteo, resultats, heure_dep.strftime('%H:%M'), heure_arr.strftime('%H:%M'), vit_moy_reelle, infos_soleil, ctx, len(points_eau), air_quality, is_past)
                st.session_state.briefing_ia = briefing
        if st.session_state.get("briefing_ia"):
            st.success("✅ Briefing prêt !")
            st.markdown(f"<div style='background-color:#f8fafc; padding:25px; border-radius:12px; border-left:6px solid #22c55e;'>{st.session_state.briefing_ia}</div>", unsafe_allow_html=True)

if __name__ == "__main__": main()
