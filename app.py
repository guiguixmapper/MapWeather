# --- FICHIER : app.py ---

"""
🚴‍♂️ Vélo & Météo — Interface propre et native
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

# Nos modules externes
import climbing as climbing_module
from climbing import detecter_ascensions, estimer_watts, estimer_fc, estimer_temps_col, calculer_calories, zones_actives, get_zone, LEGENDE_UCI, COULEURS_CAT
from weather import recuperer_fuseau, recuperer_meteo_batch, recuperer_soleil, extraire_meteo, direction_vent_relative, label_wind_chill, recuperer_qualite_air
from overpass import enrichir_cols_v2, recuperer_points_eau
from gemini_coach import generer_briefing

# NOUVEAU : On importe la fonction pour créer la carte depuis notre nouveau fichier !
from map_builder import creer_carte

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# STYLE GLOBAL
# ==============================================================================

CSS = """
<style>
  :root {
    --bleu: #2563eb; --bleu-l: #dbeafe;
    --gris: #6b7280; --border: #e2e8f0; --radius: 12px;
  }
  .app-header {
    background: linear-gradient(135deg, #1e40af 0%, #2563eb 55%, #0ea5e9 100%);
    border-radius: var(--radius); padding: 24px 32px 20px;
    margin-bottom: 20px; color: white;
  }
  .app-header h1 { font-size: 1.9rem; font-weight: 800; margin: 0; letter-spacing: -.5px; }
  .app-header p  { font-size: .9rem; margin: 5px 0 0; opacity: .85; }
  .soleil-row {
    display: flex; gap: 14px; flex-wrap: wrap;
    background: linear-gradient(90deg, #fef3c7, #fde68a);
    border-radius: var(--radius); padding: 12px 18px; margin: 10px 0; align-items: center;
  }
  .soleil-item .s-val { font-size: 1.05rem; font-weight: 700; color: #92400e; }
  .soleil-item .s-lbl { font-size: .7rem; color: #b45309; }
</style>
"""

# ==============================================================================
# UTILITAIRES GPS & HTML
# ==============================================================================

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
    except Exception as e: 
        logger.error(f"GPX : {e}"); return []

def generer_html_resume(score, ascensions, resultats, dist_tot, d_plus, d_moins, temps_s, heure_depart, heure_arr, vitesse_plat, vit_moy_reelle, calories, carte, df_profil, ref_val, mode, poids, briefing_ia=None):
    dh = int(temps_s // 3600); dm = int((temps_s % 3600) // 60)
    cols_html = ""
    for a in ascensions:
        nom = a.get("Nom", "—")
        cols_html += f"<tr><td>{a['Catégorie']}</td><td>{nom if nom != '—' else ''}</td><td>{a['Départ (km)']} km</td><td>{a['Longueur']}</td><td>{a['Dénivelé']}</td><td>{a['Pente moy.']}</td><td>{a.get('Temps col','—')}</td><td>{a.get('Arrivée sommet','—')}</td></tr>"
        
    meteo_html = ""
    valides = [cp for cp in resultats if cp.get("temp_val") is not None]
    for cp in valides:
        t = cp.get('temp_val')
        meteo_html += f"<tr><td>{cp['Heure']}</td><td>{cp['Km']} km</td><td>{cp.get('Ciel','—')}</td><td>{f'{t}°C' if t else '—'}</td><td>{cp.get('Pluie','—')}</td><td>{cp.get('vent_val','—')} km/h</td><td>{cp.get('effet','—')}</td></tr>"

    b64_map = base64.b64encode(carte.get_root().render().encode('utf-8')).decode('utf-8')
    iframe_map = f'<iframe src="data:text/html;base64,{b64_map}" style="width:100%; height:800px; border:1px solid #e2e8f0; border-radius:8px;"></iframe>'

    fig_profil = creer_figure_profil(df_profil, ascensions, vitesse_plat, ref_val, mode, poids)
    html_profil = fig_profil.to_html(full_html=False, include_plotlyjs='cdn')
    
    html_profils_cols = ""
    if ascensions:
        html_profils_cols = "<h2>🔍 Profils des montées</h2>"
        for asc in ascensions:
            fig_col = creer_figure_col(df_profil, asc)
            if fig_col: html_profils_cols += fig_col.to_html(full_html=False, include_plotlyjs=False)

    html_briefing = ""
    if briefing_ia:
        texte_formate = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', briefing_ia).replace('\n', '<br>')
        html_briefing = f"<h2>🎙️ Le Briefing du Coach IA</h2><div class='ia-box'>{texte_formate}</div>"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Roadbook Velo</title>
<style>
  body{{font-family:Arial,sans-serif;padding:32px;color:#1e293b;max-width:1200px;margin:auto}}
  h1{{color:#1e40af;border-bottom:3px solid #1e40af;padding-bottom:8px; margin-top: 0;}}
  h2{{color:#1e40af;margin-top:35px}}
  .score{{background:#1e40af;color:white;border-radius:10px;padding:14px 20px;font-size:1.1rem;font-weight:700;margin:12px 0;display:inline-block}}
  .grid{{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}}
  .card{{background:#f1f5f9;border-radius:8px;padding:12px 18px;text-align:center;flex:1;min-width:120px}}
  .card .v{{font-size:1.4rem;font-weight:700;color:#1e40af}}
  .card .l{{font-size:.72rem;color:#64748b;margin-top:3px}}
  .ia-box{{background-color:#f8fafc; padding:25px; border-radius:12px; border-left:6px solid #22c55e; color:#1e293b; font-size:1.05rem; line-height:1.6; margin-top: 15px;}}
  table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:.83rem}}
  th{{background:#1e40af;color:white;padding:8px;text-align:left}}
  td{{padding:6px 8px;border-bottom:1px solid #e2e8f0}}
  tr:nth-child(even) td{{background:#f8fafc}}
  .btn-print {{background-color: #2563eb; color: white; border: none; padding: 12px 24px; font-size: 1.1rem; border-radius: 8px; cursor: pointer; font-weight: bold; float: right;}}
  @media print {{
      .btn-print {{ display: none !important; }}
      body {{ padding: 0; max-width: 100%; }}
      .score, .card, th, .ia-box {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .ia-box, iframe, .js-plotly-plot {{ page-break-inside: avoid; }}
  }}
</style></head><body>
<button onclick="window.print()" class="btn-print">📄 Enregistrer en PDF</button>
<h1>🚴‍♂️ Carnet de route détaillé</h1>
<p>Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} · Départ prévu : {heure_depart.strftime('%d/%m/%Y %H:%M')}</p>
<div class="score">{score['label']} — {score['total']}/10 &nbsp;|&nbsp; 🌤️ {score['score_meteo']}/6 &nbsp;|&nbsp; 🏔️ {score['score_cols']}/4</div>
<div class="grid">
  <div class="card"><div class="v">{round(dist_tot/1000,1)} km</div><div class="l">📏 Distance</div></div>
  <div class="card"><div class="v">{int(d_plus)} m</div><div class="l">⬆️ D+</div></div>
  <div class="card"><div class="v">{dh}h{dm:02d}m</div><div class="l">⏱️ Durée</div></div>
  <div class="card"><div class="v" style="color:#059669">{vit_moy_reelle} km/h</div><div class="l">🚴 Moy. réelle<br>(Plat: {vitesse_plat} km/h)</div></div>
  <div class="card"><div class="v">{calories} kcal</div><div class="l">🔥 Calories</div></div>
</div>
<h2>🗺️ Carte du parcours</h2>{iframe_map}
<h2>⛰️ Profil global</h2>{html_profil}
<h2>🏔️ Liste des ascensions</h2>
{"<p>Aucune difficulté catégorisée.</p>" if not ascensions else "<table><tr><th>Cat.</th><th>Nom</th><th>Départ</th><th>Long.</th><th>D+</th><th>Pente</th><th>Temps</th><th>Arrivée</th></tr>" + cols_html + "</table>"}
{html_profils_cols}
<h2>🌤️ Météo détaillée</h2>
{"<p>Données météo indisponibles.</p>" if not meteo_html else "<table><tr><th>Heure</th><th>Km</th><th>Ciel</th><th>Temp</th><th>Pluie</th><th>Vent</th><th>Effet</th></tr>" + meteo_html + "</table>"}
{html_briefing}
</body></html>""".encode("utf-8")


# ==============================================================================
# SCORE GLOBAL ET ANALYSE
# ==============================================================================

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
        if   15 <= tm <= 22: s_temp = 2.0
        elif 10 <= tm <= 27: s_temp = 1.5
        elif  5 <= tm <= 32: s_temp = 0.8
        elif  0 <= tm:       s_temp = 0.3
        else:                s_temp = 0.0

        POIDS_EFFET = { "⬇️ Face": 1.5, "↙️ Côté (D)": 0.7, "↘️ Côté (G)": 0.7, "⬆️ Dos": -0.3, "—": 0.5 }
        ve_moy = sum((cp.get("vent_val") or 0) * POIDS_EFFET.get(cp.get("effet", "—"), 0.5) for cp in valides) / len(valides)
        if   ve_moy <= 8:  s_vent = 2.0
        elif ve_moy <= 18: s_vent = 1.5
        elif ve_moy <= 30: s_vent = 0.8
        elif ve_moy <= 45: s_vent = 0.3
        else:              s_vent = 0.0

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

def optimiser_depart(checkpoints_base, rep_list, ascensions, d_plus, vitesse, ref_val, mode, poids):
    meilleur_score = 0
    meilleur_offset = 0
    if not rep_list: return None
    for offset in [0, 1, 2, 3]:
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
# GRAPHIQUES PLOTLY
# ==============================================================================

def creer_figure_profil(df, ascensions, vitesse, ref_val, mode, poids, idx_survol=None):
    fig = go.Figure()
    dists = df["Distance (km)"].tolist()
    alts = df["Altitude (m)"].tolist()
    zones = zones_actives(mode)
    fig.add_trace(go.Scatter(x=dists, y=alts, fill="tozeroy", fillcolor="rgba(59,130,246,0.12)", line=dict(color="#3b82f6", width=2), hovertemplate="<b>Km %{x:.1f}</b><br>Altitude : %{y:.0f} m<extra></extra>", name="Profil"))
    for i, asc in enumerate(ascensions):
        d0, d1 = asc["_debut_km"], asc["_sommet_km"]
        cat = asc["Catégorie"]
        nom = asc.get("Nom", "—")
        coul = COULEURS_CAT.get(cat, "#94a3b8")
        op = 1.0 if idx_survol is None or idx_survol == i else 0.2
        sx = [d for d in dists if d0 <= d <= d1]
        sy = [alts[j] for j, d in enumerate(dists) if d0 <= d <= d1]
        if not sx: continue
        w = estimer_watts(asc["_pente_moy"], vitesse, poids)
        _, _, zcoul = get_zone(w, ref_val, zones)
        r, g, b = int(zcoul[1:3],16), int(zcoul[3:5],16), int(zcoul[5:7],16)
        hover_extra = (f"FC est. : {estimer_fc(w, ref_val, ref_val)}bpm" if mode == "🫀 Fréquence Cardiaque" else f"Puissance est. : {w} W ({round(w/ref_val*100) if ref_val>0 else '?'}% FTP)")
        fig.add_trace(go.Scatter(x=sx, y=sy, fill="tozeroy", fillcolor=f"rgba({r},{g},{b},{round(op*0.35,2)})", line=dict(color=coul, width=3 if idx_survol==i else 2), opacity=op, hovertemplate=(f"<b>{cat}{' — '+nom if nom!='—' else ''}</b><br>Km %{{x:.1f}}<br>Alt : %{{y:.0f}} m<br>{hover_extra}<extra></extra>"), name=nom if nom != "—" else cat, showlegend=True, legendgroup=cat))
        fig.add_annotation(x=d1, y=sy[-1] if sy else 0, text=f"▲ {nom if nom != '—' else cat.split()[0]}", showarrow=True, arrowhead=2, arrowsize=.8, arrowcolor=coul, font=dict(size=10, color=coul), bgcolor="white", bordercolor=coul, borderwidth=1, opacity=op)
    fig.update_layout(height=500, margin=dict(l=50,r=20,t=30,b=40), xaxis=dict(title="Distance (km)", showgrid=True, gridcolor="#e2e8f0"), yaxis=dict(title="Altitude (m)", showgrid=True, gridcolor="#e2e8f0"), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white")
    return fig

def creer_figure_col(df_profil, asc, nb_segments=None):
    d0, d1 = asc["_debut_km"], asc["_sommet_km"]
    dk = d1 - d0
    mask = [d0 <= d <= d1 for d in df_profil["Distance (km)"]]
    dists_col = [d for d, m in zip(df_profil["Distance (km)"], mask) if m]
    alts_col = [a for a, m in zip(df_profil["Altitude (m)"], mask) if m]
    if len(dists_col) < 2: return None
    seg_km = dk / nb_segments if nb_segments else (0.5 if dk < 5 else 1.0 if dk < 15 else 2.0)
    def couleur_pente(p):
        if p < 3: return "#22c55e"
        elif p < 6: return "#84cc16"
        elif p < 8: return "#eab308"
        elif p < 10: return "#f97316"
        elif p < 12: return "#ef4444"
        else: return "#7f1d1d"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dists_col, y=alts_col, fill="tozeroy", fillcolor="rgba(203,213,225,0.2)", line=dict(color="#94a3b8", width=1), hoverinfo="skip", showlegend=False))
    km_d = dists_col[0]
    while km_d < dists_col[-1] - 0.05:
        km_f = min(km_d + seg_km, dists_col[-1])
        sx = [d for d in dists_col if km_d <= d <= km_f]
        sy = [alts_col[j] for j, d in enumerate(dists_col) if km_d <= d <= km_f]
        if len(sx) >= 2:
            dist_m = (sx[-1] - sx[0]) * 1000
            pente = (max(0, sy[-1]-sy[0]) / dist_m * 100) if dist_m > 0 else 0
            coul = couleur_pente(pente)
            r, g, b = int(coul[1:3],16), int(coul[3:5],16), int(coul[5:7],16)
            fig.add_trace(go.Scatter(x=sx, y=sy, fill="tozeroy", fillcolor=f"rgba({r},{g},{b},0.4)", line=dict(color=coul, width=3), hovertemplate=f"<b>{round(pente,1)}%</b><br>Km %{{x:.1f}}<br>Alt : %{{y:.0f}} m<extra></extra>", showlegend=False))
            if dist_m > 300:
                fig.add_annotation(x=(sx[0]+sx[-1])/2, y=sy[len(sy)//2], text=f"<b>{round(pente,1)}%</b>", showarrow=False, font=dict(size=10, color=coul), bgcolor="rgba(255,255,255,0.8)", bordercolor=coul, borderwidth=1, yshift=12)
        km_d = km_f
    fig.add_trace(go.Scatter(x=dists_col, y=alts_col, mode="lines", line=dict(color="#1e293b", width=2), hovertemplate="Km %{x:.1f} — Alt : %{y:.0f} m<extra></extra>", showlegend=False))
    nom = asc.get("Nom", "—")
    titre = f"{nom+' — ' if nom != '—' else ''}{asc['Catégorie']} — {asc['Longueur']} · {asc['Dénivelé']} · {asc['Pente moy.']} moy. · {asc['Pente max']} max"
    fig.update_layout(height=380, margin=dict(l=50,r=20,t=40,b=40), xaxis=dict(title="Distance (km)", showgrid=True, gridcolor="#f1f5f9"), yaxis=dict(title="Altitude (m)", showgrid=True, gridcolor="#f1f5f9"), plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified", title=dict(text=titre, font=dict(size=13, color="#1e293b"), x=0))
    return fig

def creer_figure_meteo(resultats):
    kms, temps, vents, rafales, pluies, cv, cp_ = [], [], [], [], [], [], []
    for r in resultats:
        t = r.get("temp_val"); v = r.get("vent_val")
        if t is None or v is None: continue
        kms.append(r["Km"]); temps.append(t); vents.append(v); rafales.append(r.get("rafales_val") or v)
        pluies.append(r.get("pluie_pct") or 0)
        cv.append("#ef4444" if v>=40 else "#f97316" if v>=25 else "#eab308" if v>=10 else "#22c55e")
        p = r.get("pluie_pct") or 0
        cp_.append("#1d4ed8" if p>=70 else "#2563eb" if p>=40 else "#60a5fa" if p>=20 else "#bfdbfe")
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.40, 0.33, 0.27], vertical_spacing=0.06, subplot_titles=["🌡️ Température (°C)", "💨 Vent moyen & Rafales (km/h)", "🌧️ Probabilité de pluie (%)"])
    if kms:
        ct = ["#8b5cf6" if t<5 else "#3b82f6" if t<15 else "#22c55e" if t<22 else "#f97316" if t<30 else "#ef4444" for t in temps]
        fig.add_trace(go.Scatter(x=kms, y=temps, mode="lines+markers", line=dict(color="#f97316", width=2.5), marker=dict(color=ct, size=9, line=dict(color="white", width=1.5)), hovertemplate="<b>Km %{x}</b><br>Temp : %{y}°C<extra></extra>", name="Température"), row=1, col=1)
        fig.add_hrect(y0=15, y1=22, row=1, col=1, fillcolor="rgba(34,197,94,0.10)", line_width=0, annotation_text="Zone idéale (15–22°C)", annotation_font_size=9, annotation_font_color="#16a34a", annotation_position="top left")
        fig.add_trace(go.Bar(x=kms, y=vents, marker_color=cv, name="Vent moyen", hovertemplate="<b>Km %{x}</b><br>Vent : %{y} km/h<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Scatter(x=kms, y=rafales, mode="lines+markers", line=dict(color="#475569", width=1.8, dash="dot"), marker=dict(size=5, color="#475569"), name="Rafales", hovertemplate="<b>Km %{x}</b><br>Rafales : %{y} km/h<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Bar(x=kms, y=pluies, marker_color=cp_, name="Pluie", hovertemplate="<b>Km %{x}</b><br>Pluie : %{y}%<extra></extra>"), row=3, col=1)
        fig.add_hline(y=50, row=3, col=1, line_dash="dot", line_color="#64748b", line_width=1.5, annotation_text="Seuil 50%", annotation_font_size=9, annotation_font_color="#64748b", annotation_position="top right")
    fig.update_layout(height=620, margin=dict(l=55,r=20,t=45,b=40), hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white", showlegend=False, dragmode=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=1, col=1, title_text="°C")
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=2, col=1, title_text="km/h", rangemode="tozero")
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=3, col=1, title_text="%", range=[0,105])
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", row=1, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", row=2, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", title_text="Distance (km)", row=3, col=1)
    return fig

# ==============================================================================
# MAIN
# ==============================================================================
def main():
    st.set_page_config(page_title="Vélo & Météo", page_icon="🚴‍♂️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("<div class='app-header'><h1>🚴‍♂️ Vélo & Météo</h1><p>Analysez votre tracé GPX : météo en temps réel, cols UCI, profil interactif et zones d'entraînement.</p></div>", unsafe_allow_html=True)

    # ── SIDEBAR ──
    st.sidebar.header("⚙️ Paramètres")
    fichier = st.sidebar.file_uploader("📂 Fichier GPX", type=["gpx"])
    st.sidebar.divider()
    date_dep = st.sidebar.date_input("📅 Date de départ", value=date.today())
    heure_dep = st.sidebar.time_input("🕐 Heure de départ")
    vitesse = st.sidebar.number_input("🚴 Vitesse moy. plat (km/h)", 5, 60, 25)
    st.sidebar.divider()
    mode = st.sidebar.radio("📊 Mode d'analyse", ["⚡ Puissance", "🫀 Fréquence Cardiaque"], horizontal=True)
    if mode == "⚡ Puissance":
        ref_val = st.sidebar.number_input("⚡ FTP (W)", 50, 500, 220)
        fc_max = None; ftp_fc = ref_val
        poids = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)
    else:
        ref_val = st.sidebar.number_input("❤️ FC max (bpm)", 100, 220, 185)
        fc_max = ref_val; ftp_fc = st.sidebar.number_input("⚡ FTP estimé (W)", 50, 500, 220)
        poids = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)
    st.sidebar.divider()
    intervalle = st.sidebar.selectbox("⏱️ Intervalle checkpoints météo", options=[5,10,15], index=1, format_func=lambda x: f"Toutes les {x} min")
    intervalle_sec = intervalle * 60
    
    st.sidebar.divider()
    with st.sidebar.expander("🏔️ Détection des montées", expanded=False):
        if "sensibilite" not in st.session_state: st.session_state.sensibilite = 3
        if "seuil_debut" not in st.session_state: st.session_state.seuil_debut = float(climbing_module.SEUIL_DEBUT)
        if "seuil_fin" not in st.session_state: st.session_state.seuil_fin = float(climbing_module.SEUIL_FIN)
        if "fusion_m" not in st.session_state: st.session_state.fusion_m = int(climbing_module.MAX_DESCENTE_FUSION_M)
        st.slider("🎚️ Sensibilité de détection", 1, 5, step=1, key="sensibilite")
        niv = st.session_state.sensibilite
        if st.button("↺ Réinitialiser", use_container_width=True):
            st.session_state["_reset_demande"] = True; st.rerun()
        if st.session_state.pop("_reset_demande", False):
            for k in ["sensibilite", "seuil_debut", "seuil_fin", "fusion_m", "_last_sensibilite"]: st.session_state.pop(k, None)
            st.rerun()
        with st.expander("⚙️ Réglages fins", expanded=False):
            PARAMS = {1: (4.0, 2.0, 20), 2: (3.0, 1.5, 35), 3: (2.0, 1.0, 50), 4: (1.5, 0.5, 70), 5: (0.5, 0.0, 100)}
            if st.session_state.get("_last_sensibilite") != niv:
                st.session_state.seuil_debut, st.session_state.seuil_fin, st.session_state.fusion_m = PARAMS[niv]
                st.session_state["_last_sensibilite"] = niv
            st.slider("Seuil de départ (%)", 0.5, 5.0, step=0.5, key="seuil_debut")
            st.slider("Seuil de fin (%)", 0.0, 3.0, step=0.5, key="seuil_fin")
            st.slider("Fusion (D− max, m)", 10, 200, step=10, key="fusion_m")
        climbing_module.SEUIL_DEBUT = st.session_state.seuil_debut
        climbing_module.SEUIL_FIN = st.session_state.seuil_fin
        climbing_module.MAX_DESCENTE_FUSION_M = st.session_state.fusion_m

    st.sidebar.divider()
    with st.sidebar.expander("🔧 Options avancées", expanded=False):
        noms_osm = st.toggle("🗺️ Nommer les cols (OpenStreetMap)", value=False)
        gemini_key = st.text_input("🤖 Clé API Gemini", value="", type="password")

    ph_fuseau = st.sidebar.empty()
    ph_fuseau.info("🌍 Fuseau : en attente…")

    if not fichier: st.info("👈 Importez un fichier GPX."); return

    delta_jours = (date_dep - date.today()).days
    is_past = delta_jours < 0

    etapes = st.empty()
    with etapes.container():
        with st.spinner("📍 Lecture du fichier GPX…"):
            points_gpx = parser_gpx(fichier.read())
    if not points_gpx: st.error("❌ Fichier vide ou corrompu."); return

    coords_gpx = tuple((p.latitude, p.longitude) for p in points_gpx)
    fuseau = recuperer_fuseau(coords_gpx[0][0], coords_gpx[0][1])
    ph_fuseau.success(f"🌍 **{fuseau}**")
    date_depart = datetime.combine(date_dep, heure_dep)
    infos_soleil = recuperer_soleil(coords_gpx[0][0], coords_gpx[0][1], date_dep.strftime("%Y-%m-%d"))

    with etapes.container():
        with st.spinner("📐 Calcul du parcours…"):
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
    pf = points_gpx[-1]
    checkpoints.append({"lat": pf.latitude, "lon": pf.longitude, "Cap": cap, "Heure": heure_arr.strftime("%d/%m %H:%M") + " 🏁", "Heure_API": heure_arr.replace(minute
