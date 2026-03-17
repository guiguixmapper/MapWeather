"""
🚴‍♂️ Vélo & Météo — v4
Nouveautés : mode Puissance/FC dans la sidebar, import GPX dans la sidebar,
carte 700px + zoom molette, graphique météo 3 panneaux indépendants + pan désactivé.
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
    border-radius: var(--radius);
    padding: 24px 32px 20px;
    margin-bottom: 20px;
    color: white;
  }
  .app-header h1 { font-size: 1.9rem; font-weight: 800; margin: 0; letter-spacing: -.5px; }
  .app-header p  { font-size: .9rem; margin: 5px 0 0; opacity: .85; }

  .metric-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px; margin: 14px 0;
  }
  .metric-card {
    background: #fff; border: 1px solid var(--border);
    border-radius: var(--radius); padding: 14px;
    text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.05);
  }
  .metric-card .val { font-size: 1.45rem; font-weight: 700; color: #1e293b; line-height: 1.2; }
  .metric-card .lbl { font-size: .72rem; color: var(--gris); margin-top: 3px; }

  .score-card {
    background: linear-gradient(135deg, #1e3a5f, #1e40af);
    border-radius: var(--radius); padding: 22px 26px;
    color: white; margin: 14px 0;
    display: flex; align-items: center; gap: 26px; flex-wrap: wrap;
  }
  .score-note  { font-size: 3.2rem; font-weight: 900; line-height: 1; }
  .score-label { font-size: 1.05rem; font-weight: 600; margin-top: 3px; }
  .score-sub   { font-size: .8rem; opacity: .7; margin-top: 2px; }
  .score-pills { display: flex; gap: 9px; flex-wrap: wrap; }
  .pill {
    background: rgba(255,255,255,.15); border-radius: 20px;
    padding: 5px 13px; font-size: .8rem;
  }
  .soleil-row {
    display: flex; gap: 14px; flex-wrap: wrap;
    background: linear-gradient(90deg, #fef3c7, #fde68a);
    border-radius: var(--radius); padding: 12px 18px;
    margin: 10px 0; align-items: center;
  }
  .soleil-item .s-val { font-size: 1.05rem; font-weight: 700; color: #92400e; }
  .soleil-item .s-lbl { font-size: .7rem; color: #b45309; }
  @media (max-width: 640px) {
    .app-header h1 { font-size: 1.35rem; }
    .score-card { flex-direction: column; gap: 12px; }
    .score-note { font-size: 2.4rem; }
  }
</style>
"""

# ==============================================================================
# ZONES D'ENTRAÎNEMENT
# ==============================================================================

# ── Puissance (% FTP) ──
ZONES_PUISSANCE = [
    (0,    0.55, 1, "Z1 Récup",      "#94a3b8"),
    (0.55, 0.75, 2, "Z2 Endurance",  "#3b82f6"),
    (0.75, 0.90, 3, "Z3 Tempo",      "#22c55e"),
    (0.90, 1.05, 4, "Z4 Seuil",      "#eab308"),
    (1.05, 1.20, 5, "Z5 VO2max",     "#f97316"),
    (1.20, 999,  6, "Z6 Anaérobie",  "#ef4444"),
]

# ── Fréquence cardiaque (% FC max) ──
ZONES_FC = [
    (0,    0.60, 1, "Z1 Récup",      "#94a3b8"),
    (0.60, 0.70, 2, "Z2 Endurance",  "#3b82f6"),
    (0.70, 0.80, 3, "Z3 Tempo",      "#22c55e"),
    (0.80, 0.90, 4, "Z4 Seuil",      "#eab308"),
    (0.90, 0.95, 5, "Z5 VO2max",     "#f97316"),
    (0.95, 999,  6, "Z6 Anaérobie",  "#ef4444"),
]


def get_zone(valeur, ref, zones):
    """Retourne (num_zone, label, couleur) selon le ratio valeur/ref."""
    ratio = valeur / ref if ref > 0 else 0
    for bas, haut, num, lbl, coul in zones:
        if bas <= ratio < haut:
            return num, lbl, coul
    return 6, "Z6 Anaérobie", "#ef4444"


def zones_actives(mode):
    """Retourne la liste de zones selon le mode."""
    return ZONES_PUISSANCE if mode == "⚡ Puissance" else ZONES_FC


# ==============================================================================
# SECTION 1 : UTILITAIRES
# ==============================================================================

def calculer_cap(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def direction_vent_relative(cap, dir_vent):
    diff = (dir_vent - cap) % 360
    if diff <= 45 or diff >= 315:  return "⬇️ Face"
    elif 135 <= diff <= 225:       return "⬆️ Dos"
    elif 45 < diff < 135:          return "↘️ Côté (D)"
    else:                          return "↙️ Côté (G)"


def obtenir_icone_meteo(code):
    m = {
        0:"☀️ Clair", 1:"⛅ Éclaircies", 2:"⛅ Éclaircies", 3:"☁️ Couvert",
        45:"🌫️ Brouillard", 48:"🌫️ Brouillard",
        51:"🌦️ Bruine", 53:"🌦️ Bruine", 55:"🌦️ Bruine",
        61:"🌧️ Pluie", 63:"🌧️ Pluie", 65:"🌧️ Pluie",
        66:"🌧️ Pluie", 67:"🌧️ Pluie", 80:"🌧️ Pluie", 81:"🌧️ Pluie", 82:"🌧️ Pluie",
        71:"❄️ Neige", 73:"❄️ Neige", 75:"❄️ Neige", 77:"❄️ Neige",
        85:"❄️ Neige", 86:"❄️ Neige",
        95:"⛈️ Orage", 96:"⛈️ Orage", 99:"⛈️ Orage",
    }
    return m.get(code, "❓ Inconnu")


def wind_chill(temp_c, vent_kmh):
    if temp_c > 10 or vent_kmh <= 4.8: return None
    return round(13.12 + 0.6215*temp_c - 11.37*(vent_kmh**0.16) + 0.3965*temp_c*(vent_kmh**0.16))


def label_wind_chill(r):
    if r is None:   return "—"
    if r <= -40:    return f"🟣 {r}°C (Danger extrême)"
    if r <= -27:    return f"🔴 {r}°C (Très dangereux)"
    if r <= -10:    return f"🟠 {r}°C (Dangereux)"
    if r <= 0:      return f"🟡 {r}°C (Froid intense)"
    return                 f"🔵 {r}°C (Frais)"


def estimer_watts(pente_pct, vitesse_kmh, poids_kg=75):
    g  = 9.81
    vm = vitesse_kmh / 3.6
    pr = math.atan(pente_pct / 100)
    return max(0, int(poids_kg * g * math.sin(pr) * vm + poids_kg * g * 0.004 * vm))


def estimer_fc(watts, ftp, fc_max, fc_repos=50):
    """
    Estimation FC à partir des watts via relation linéaire simplifiée.
    FC = FC_repos + (watts/FTP) * (FC_max - FC_repos) * 0.85
    """
    if ftp <= 0 or fc_max <= 0: return None
    ratio = min(watts / ftp, 1.3)
    return int(fc_repos + ratio * (fc_max - fc_repos) * 0.85)


# ==============================================================================
# SECTION 2 : UCI
# ==============================================================================

SEUILS_UCI = {"🔴 HC":80, "🟠 1ère Cat.":40, "🟡 2ème Cat.":20, "🟢 3ème Cat.":8, "🔵 4ème Cat.":2}

def categoriser_uci(distance_m, d_plus):
    if distance_m < 300 or d_plus < 10: return None, 0
    pm = (d_plus / distance_m) * 100
    if pm < 2.0: return None, 0
    score = (d_plus * pm) / 100
    for lbl, seuil in SEUILS_UCI.items():
        if score >= seuil: return lbl, round(score, 2)
    return None, 0


# ==============================================================================
# SECTION 3 : DÉTECTION ASCENSIONS
# ==============================================================================

def lisser(alts, f=5):
    demi, n, r = f//2, len(alts), []
    for i in range(n):
        s, e = max(0,i-demi), min(n,i+demi+1)
        r.append(sum(alts[s:e])/(e-s))
    return r

def detecter_segments(dists, alts):
    segs, n = [], len(alts)
    en_m = False; ci = si = 0
    for i in range(1, n):
        a = alts[i]
        if not en_m:
            if a < alts[ci]: ci = i
            elif a >= alts[ci] + 10: en_m = True; si = i
        else:
            if a > alts[si]: si = i
            elif a <= alts[si] - 30: segs.append((ci, si)); en_m = False; ci = si = i
    if en_m and si > ci: segs.append((ci, si))
    return segs

def fusionner(segs, alts):
    if not segs: return []
    f = [segs[0]]
    for d, s in segs[1:]:
        pd_, ps_ = f[-1]
        if alts[ps_] - alts[d] <= 25:
            f[-1] = (pd_, s if alts[s] >= alts[ps_] else ps_)
        else:
            f.append((d, s))
    return f

def pente_max(dists, alts, d0, s0):
    pm = 0.0
    for i in range(d0+1, s0+1):
        for j in range(i-1, max(d0-1,i-50), -1):
            dd = (dists[i]-dists[j])*1000
            if dd >= 50:
                p = ((alts[i]-alts[j])/dd)*100
                if 0 < p <= 40: pm = max(pm, p)
                break
    return round(pm, 1)

def detecter_ascensions(df):
    if df.empty or len(df) < 3: return []
    alts  = df["Altitude (m)"].tolist()
    dists = df["Distance (km)"].tolist()
    al    = lisser(alts)
    segs  = fusionner(detecter_segments(dists, al), al)
    out   = []
    for d0, s0 in segs:
        dk  = dists[s0] - dists[d0]
        dp  = alts[s0]  - alts[d0]
        if dk <= 0 or dp <= 0: continue
        cat, score = categoriser_uci(dk*1000, dp)
        if cat is None: continue
        pm_ = (dp/(dk*1000))*100
        out.append({
            "Catégorie":   cat,   "Départ (km)": round(dists[d0],1),
            "Sommet (km)": round(dists[s0],1),  "Longueur": f"{round(dk,1)} km",
            "Dénivelé":    f"{int(dp)} m",       "Pente moy.": f"{round(pm_,1)} %",
            "Pente max":   f"{pente_max(dists,alts,d0,s0)} %",
            "Alt. sommet": f"{int(alts[s0])} m", "Score UCI": score,
            "_debut_km": dists[d0], "_sommet_km": dists[s0], "_pente_moy": pm_,
        })
    out.sort(key=lambda x: x["_debut_km"])
    return out


# ==============================================================================
# SECTION 4 : API (cache)
# ==============================================================================

@st.cache_data(show_spinner=False)
def parser_gpx(data):
    try:
        gpx = gpxpy.parse(data)
        return [p for t in gpx.tracks for s in t.segments for p in s.points]
    except Exception as e:
        logger.error(f"GPX : {e}"); return []

@st.cache_data(show_spinner=False)
def recuperer_fuseau(lat, lon):
    try:
        r = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m&timezone=auto", timeout=10)
        r.raise_for_status(); return r.json().get("timezone","UTC")
    except Exception as e:
        logger.warning(f"Fuseau : {e}"); return "UTC"

@st.cache_data(ttl=1800, show_spinner=False)
def recuperer_meteo_batch(cps):
    if not cps: return []
    lats = ",".join(str(c[0]) for c in cps)
    lons = ",".join(str(c[1]) for c in cps)
    url  = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&hourly=temperature_2m,precipitation_probability,weathercode,"
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m&timezone=auto"
    )
    try:
        r = requests.get(url, timeout=15); r.raise_for_status()
        d = r.json(); return d if isinstance(d, list) else [d]
    except Exception as e:
        logger.error(f"Météo : {e}"); return None

@st.cache_data(show_spinner=False)
def recuperer_soleil(lat, lon, date_str):
    try:
        r = requests.get(f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&date={date_str}&formatted=0", timeout=10)
        r.raise_for_status(); d = r.json()
        if d.get("status") != "OK": return None
        return {"lever": datetime.fromisoformat(d["results"]["sunrise"]),
                "coucher": datetime.fromisoformat(d["results"]["sunset"])}
    except Exception as e:
        logger.warning(f"Soleil : {e}"); return None

def extraire_meteo(api, heure):
    vide = dict(Ciel="—",temp_val=None,Pluie="—",pluie_pct=None,
                vent_val=None,rafales_val=None,Dir="—",dir_deg=None,effet="—",ressenti=None)
    if not api or "hourly" not in api: return vide
    hs = api["hourly"].get("time",[])
    if heure not in hs: return vide
    idx = hs.index(heure); h = api["hourly"]
    def sg(k,d=None): v=h.get(k,[]); return v[idx] if idx<len(v) else d
    dd    = sg("wind_direction_10m")
    dirs  = ["N","NE","E","SE","S","SO","O","NO"]
    dl    = dirs[round(dd/45)%8] if dd is not None else "—"
    temp  = sg("temperature_2m"); vent = sg("wind_speed_10m")
    pp    = sg("precipitation_probability")
    try:    pct = int(pp)
    except: pct = None
    return {
        "Ciel": obtenir_icone_meteo(sg("weathercode",0)),
        "temp_val": temp, "Pluie": f"{pct}%" if pct is not None else "—",
        "pluie_pct": pct, "vent_val": vent, "rafales_val": sg("wind_gusts_10m"),
        "Dir": dl, "dir_deg": dd, "effet": "—",
        "ressenti": wind_chill(temp,vent) if (temp is not None and vent is not None) else None,
    }


# ==============================================================================
# SECTION 5 : SCORE GLOBAL
# ==============================================================================

def calculer_score(resultats, ascensions, d_plus, vitesse, ref_val, mode, poids):
    valides = [cp for cp in resultats if cp.get("temp_val") is not None]
    sm = 4.0
    if valides:
        tm = sum(cp["temp_val"] for cp in valides)/len(valides)
        vm = sum(cp.get("vent_val") or 0 for cp in valides)/len(valides)
        pm = sum(cp.get("pluie_pct") or 0 for cp in valides)/len(valides)
        sm -= (0 if 15<=tm<=22 else 0.5 if 10<=tm<=28 else 1.5 if 5<=tm<=32 else 2.5)
        sm -= (1.5 if vm>40 else 1.0 if vm>25 else 0.5 if vm>15 else 0)
        sm -= (1.5 if pm>70 else 1.0 if pm>40 else 0.3 if pm>20 else 0)
    else: sm = 2.0
    sc = 3.0 if d_plus<500 else 2.0 if d_plus<1500 else 1.0 if d_plus<3000 else 0.5
    cats = [a["Catégorie"] for a in ascensions]
    sc = max(0, sc + cats.count("🔴 HC")*-0.5 + cats.count("🟠 1ère Cat.")*-0.3 + cats.count("🟡 2ème Cat.")*-0.1)
    se = 3.0
    if ascensions and ref_val > 0:
        wl = [estimer_watts(a["_pente_moy"], vitesse, poids) for a in ascensions]
        wm = sum(wl)/len(wl)
        pct = wm/ref_val if mode == "⚡ Puissance" else 0.85  # FC : effort moyen estimé
        se = (0.5 if pct>1.10 else 1.0 if pct>0.95 else 1.5 if pct>0.80 else 2.5 if pct>0.60 else 3.0)
    total = round(min(10, max(0, sm+sc+se)), 1)
    lbl = ("🔴 Très difficile" if total<4 else "🟠 Difficile" if total<6
           else "🟡 Engagée" if total<7.5 else "🟢 Bonne sortie" if total<9 else "⭐ Idéale")
    return {"total":total,"label":lbl,"score_meteo":round(max(0,sm),1),"score_cols":round(sc,1),"score_effort":round(se,1)}


# ==============================================================================
# SECTION 6 : GRAPHIQUES
# ==============================================================================

COULEURS_CAT = {
    "🔴 HC":"#ef4444","🟠 1ère Cat.":"#f97316",
    "🟡 2ème Cat.":"#eab308","🟢 3ème Cat.":"#22c55e","🔵 4ème Cat.":"#3b82f6",
}

def creer_figure_profil(df, ascensions, vitesse, ref_val, mode, poids, idx_survol=None):
    fig   = go.Figure()
    dists = df["Distance (km)"].tolist()
    alts  = df["Altitude (m)"].tolist()
    zones = zones_actives(mode)

    fig.add_trace(go.Scatter(
        x=dists, y=alts, fill="tozeroy",
        fillcolor="rgba(59,130,246,0.12)",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="<b>Km %{x:.1f}</b><br>Altitude : %{y:.0f} m<extra></extra>",
        name="Profil",
    ))

    for i, asc in enumerate(ascensions):
        d0, d1  = asc["_debut_km"], asc["_sommet_km"]
        cat     = asc["Catégorie"]
        coul    = COULEURS_CAT.get(cat, "#94a3b8")
        op      = 1.0 if idx_survol is None or idx_survol==i else 0.2
        sx      = [d for d in dists if d0<=d<=d1]
        sy      = [alts[j] for j,d in enumerate(dists) if d0<=d<=d1]
        if not sx: continue

        w       = estimer_watts(asc["_pente_moy"], vitesse, poids)
        _, zlbl, zcoul = get_zone(w, ref_val, zones)
        r,g,b   = int(zcoul[1:3],16), int(zcoul[3:5],16), int(zcoul[5:7],16)

        hover_extra = (f"FC est. : {estimer_fc(w, ref_val, ref_val)}bpm"
                       if mode == "🫀 Fréquence Cardiaque"
                       else f"Puissance est. : {w} W ({round(w/ref_val*100) if ref_val>0 else '?'}% FTP)")

        fig.add_trace(go.Scatter(
            x=sx, y=sy, fill="tozeroy",
            fillcolor=f"rgba({r},{g},{b},{round(op*0.35,2)})",
            line=dict(color=coul, width=3 if idx_survol==i else 2),
            opacity=op,
            hovertemplate=f"<b>{cat}</b> — {zlbl}<br>Km %{{x:.1f}}<br>Alt : %{{y:.0f}} m<br>{hover_extra}<extra></extra>",
            name=f"{cat} · {zlbl}",
        ))
        fig.add_annotation(
            x=d1, y=sy[-1] if sy else 0,
            text=f"▲ {cat.split()[0]}",
            showarrow=True, arrowhead=2, arrowsize=.8,
            arrowcolor=coul, font=dict(size=10,color=coul),
            bgcolor="white", bordercolor=coul, borderwidth=1, opacity=op,
        )

    fig.update_layout(
        height=360, margin=dict(l=50,r=20,t=30,b=40),
        xaxis=dict(title="Distance (km)", showgrid=True, gridcolor="#f1f5f9"),
        yaxis=dict(title="Altitude (m)",  showgrid=True, gridcolor="#f1f5f9"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


def creer_figure_meteo(resultats):
    """
    3 graphiques empilés indépendants avec dragmode désactivé.
    Température / Vent+Rafales / Pluie — séparés, pas superposés.
    """
    kms, temps, vents, rafales, pluies = [], [], [], [], []
    cv, cp_ = [], []

    for r in resultats:
        t = r.get("temp_val"); v = r.get("vent_val")
        if t is None or v is None: continue
        kms.append(r["Km"]); temps.append(t)
        vents.append(v); rafales.append(r.get("rafales_val") or v)
        pluies.append(r.get("pluie_pct") or 0)
        cv.append("#ef4444" if v>=40 else "#f97316" if v>=25 else "#eab308" if v>=10 else "#22c55e")
        p = r.get("pluie_pct") or 0
        cp_.append("#1d4ed8" if p>=70 else "#2563eb" if p>=40 else "#60a5fa" if p>=20 else "#bfdbfe")

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.40, 0.33, 0.27],
        vertical_spacing=0.06,
        subplot_titles=[
            "🌡️ Température (°C)",
            "💨 Vent moyen & Rafales (km/h)",
            "🌧️ Probabilité de pluie (%)",
        ],
    )

    if kms:
        # ── Température ──
        ct = ["#8b5cf6" if t<5 else "#3b82f6" if t<15 else "#22c55e" if t<22
              else "#f97316" if t<30 else "#ef4444" for t in temps]

        fig.add_trace(go.Scatter(
            x=kms, y=temps, mode="lines+markers",
            line=dict(color="#f97316", width=2.5),
            marker=dict(color=ct, size=9, line=dict(color="white", width=1.5)),
            hovertemplate="<b>Km %{x}</b><br>Temp : %{y}°C<extra></extra>",
            name="Température",
        ), row=1, col=1)

        # Zone de confort
        fig.add_hrect(y0=15, y1=22, row=1, col=1,
            fillcolor="rgba(34,197,94,0.10)", line_width=0,
            annotation_text="Zone idéale (15–22°C)",
            annotation_font_size=9, annotation_font_color="#16a34a",
            annotation_position="top left")

        # ── Vent ──
        fig.add_trace(go.Bar(
            x=kms, y=vents, marker_color=cv, name="Vent moyen",
            hovertemplate="<b>Km %{x}</b><br>Vent : %{y} km/h<extra></extra>",
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=kms, y=rafales, mode="lines+markers",
            line=dict(color="#475569", width=1.8, dash="dot"),
            marker=dict(size=5, color="#475569"),
            name="Rafales",
            hovertemplate="<b>Km %{x}</b><br>Rafales : %{y} km/h<extra></extra>",
        ), row=2, col=1)

        # ── Pluie ──
        fig.add_trace(go.Bar(
            x=kms, y=pluies, marker_color=cp_, name="Pluie",
            hovertemplate="<b>Km %{x}</b><br>Pluie : %{y}%<extra></extra>",
        ), row=3, col=1)
        fig.add_hline(y=50, row=3, col=1,
            line_dash="dot", line_color="#64748b", line_width=1.5,
            annotation_text="Seuil 50%",
            annotation_font_size=9, annotation_font_color="#64748b",
            annotation_position="top right")

    fig.update_layout(
        height=620,
        margin=dict(l=55, r=20, t=45, b=40),
        hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        showlegend=False,
        dragmode=False,          # ← désactive le pan accidentel
    )
    # Axes Y
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=1, col=1, title_text="°C")
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=2, col=1, title_text="km/h", rangemode="tozero")
    fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=3, col=1, title_text="%", range=[0,105])
    # Axes X
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", row=1, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", row=2, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", title_text="Distance (km)", row=3, col=1)

    return fig


# ==============================================================================
# SECTION 7 : CARTE
# ==============================================================================

def creer_carte(points_gpx, resultats):
    carte = folium.Map(
        location=[points_gpx[0].latitude, points_gpx[0].longitude],
        zoom_start=11, tiles="CartoDB positron",
        scrollWheelZoom=True,   # ← zoom molette réactivé (carte dans son onglet)
    )
    folium.PolyLine([[p.latitude,p.longitude] for p in points_gpx],
                    color="#2563eb", weight=5, opacity=0.9).add_to(carte)
    folium.Marker([points_gpx[0].latitude, points_gpx[0].longitude],
                  tooltip="🚦 Départ",
                  icon=folium.Icon(color="green",icon="play",prefix="fa")).add_to(carte)
    folium.Marker([points_gpx[-1].latitude, points_gpx[-1].longitude],
                  tooltip="🏁 Arrivée",
                  icon=folium.Icon(color="red",icon="flag",prefix="fa")).add_to(carte)

    for cp in resultats:
        t = cp.get("temp_val")
        if t is None: continue
        dd = cp.get("dir_deg"); vv = cp.get("vent_val",0) or 0
        fc = "#ef4444" if vv>=40 else "#f97316" if vv>=25 else "#eab308" if vv>=10 else "#22c55e"
        rot = (dd+180)%360 if dd is not None else 0
        svg = (f'<svg width="16" height="16" viewBox="0 0 28 28" style="vertical-align:middle">'
               f'<g transform="rotate({rot},14,14)"><polygon points="14,2 20,22 14,18 8,22" fill="{fc}"/>'
               f'</g></svg>') if dd is not None else "💨"

        pp = cp.get("pluie_pct")
        if pp is not None:
            pc = "#1d4ed8" if pp>=70 else "#2563eb" if pp>=40 else "#60a5fa"
            barre = (f'<div style="margin:4px 0 2px;font-size:11px">&#127783; Pluie : <b>{pp}%</b></div>'
                     '<div style="background:#e2e8f0;border-radius:4px;height:6px;width:100%">'
                     f'<div style="background:{pc};width:{pp}%;height:6px;border-radius:4px"></div></div>')
        else:
            barre = '<div style="font-size:11px">&#127783; Pluie : —</div>'

        res = cp.get("ressenti")
        popup = (
            '<div style="font-family:sans-serif;font-size:12px;min-width:200px">'
            f'<div style="font-weight:700;font-size:13px;border-bottom:1px solid #e2e8f0;padding-bottom:4px;margin-bottom:6px">'
            f'{cp["Heure"]} — Km {cp["Km"]}</div>'
            f'<div style="color:#6b7280;margin-bottom:5px">⛰️ Alt : {cp["Alt (m)"]} m</div>'
            f'<div style="font-size:15px;margin-bottom:3px">{cp["Ciel"]} <b>{t}°C</b>'
            + (f' <span style="color:#6b7280;font-size:11px">(ressenti {res}°C)</span>' if res is not None else "")
            + f'</div>{barre}'
            f'<div style="margin-top:7px;padding-top:5px;border-top:1px solid #f1f5f9">'
            f'<div style="display:flex;align-items:center;gap:5px;margin-bottom:2px">'
            f'{svg} <b>{vv} km/h</b> <span style="color:#6b7280">du {cp["Dir"]}</span></div>'
            f'<div style="color:#6b7280;font-size:11px">Rafales : {cp.get("rafales_val","—")} km/h</div>'
            f'<div style="margin-top:3px;font-size:11px">🚴 <b>{cp.get("effet","—")}</b></div>'
            '</div></div>'
        )
        rot_str = str(rot)
        tooltip = (
            f"{cp['Heure']} | {cp['Ciel']} {t}°C | "
            f'<svg width="12" height="12" viewBox="0 0 28 28" style="vertical-align:middle">'
            f'<g transform="rotate({rot_str},14,14)"><polygon points="14,2 20,22 14,18 8,22" fill="{fc}"/></g></svg>'
            f" {vv} km/h"
        )
        folium.Marker([cp["lat"],cp["lon"]],
            popup=folium.Popup(popup, max_width=280),
            tooltip=folium.Tooltip(tooltip, sticky=True),
            icon=folium.Icon(color="blue",icon="info-sign"),
        ).add_to(carte)
    return carte


# ==============================================================================
# SECTION 8 : APPLICATION PRINCIPALE
# ==============================================================================

def main():
    st.set_page_config(page_title="Vélo & Météo", page_icon="🚴‍♂️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    st.markdown("""
    <div class="app-header">
      <h1>🚴‍♂️ Vélo &amp; Météo</h1>
      <p>Analysez votre tracé GPX : météo en temps réel, cols UCI, profil interactif et zones d'entraînement.</p>
    </div>""", unsafe_allow_html=True)

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    st.sidebar.header("⚙️ Paramètres")

    # Import GPX en sidebar (compact)
    fichier = st.sidebar.file_uploader("📂 Fichier GPX", type=["gpx"])

    st.sidebar.divider()
    date_dep  = st.sidebar.date_input("📅 Date de départ", value=date.today())
    heure_dep = st.sidebar.time_input("🕐 Heure de départ")
    vitesse   = st.sidebar.number_input("🚴 Vitesse moy. plat (km/h)", 5, 60, 25)

    st.sidebar.divider()

    # Toggle Puissance / FC
    mode = st.sidebar.radio("📊 Mode d'analyse", ["⚡ Puissance", "🫀 Fréquence Cardiaque"],
                             horizontal=True)

    if mode == "⚡ Puissance":
        ref_val = st.sidebar.number_input("⚡ FTP (W)", 50, 500, 220,
                    help="Puissance seuil fonctionnelle.")
        poids = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)
    else:
        ref_val = st.sidebar.number_input("❤️ FC max (bpm)", 100, 220, 185,
                    help="Fréquence cardiaque maximale.")
        poids = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)

    st.sidebar.divider()
    intervalle     = st.sidebar.selectbox("⏱️ Intervalle checkpoints météo",
                       options=[5,10,15], index=1,
                       format_func=lambda x: f"Toutes les {x} min")
    intervalle_sec = intervalle * 60

    # Légende zones dans la sidebar
    st.sidebar.divider()
    st.sidebar.markdown("**Zones d'entraînement**")
    for _, _, num, lbl, coul in zones_actives(mode):
        st.sidebar.markdown(
            f'<span style="background:{coul};color:white;border-radius:4px;'
            f'padding:2px 8px;font-size:.75rem">{lbl}</span>',
            unsafe_allow_html=True)

    ph_fuseau = st.sidebar.empty()
    ph_fuseau.info("🌍 Fuseau : en attente…")

    if fichier is None:
        st.info("👈 Importez un fichier GPX dans la barre latérale pour commencer l'analyse.")
        return

    # ── CHARGEMENT ────────────────────────────────────────────────────────────
    etapes = st.empty()
    with etapes.container():
        with st.spinner("📍 Lecture du fichier GPX…"):
            points_gpx = parser_gpx(fichier.read())
    if not points_gpx:
        st.error("❌ Fichier GPX vide ou corrompu."); return

    with etapes.container():
        with st.spinner("🌍 Fuseau horaire…"):
            fuseau = recuperer_fuseau(points_gpx[0].latitude, points_gpx[0].longitude)
    ph_fuseau.success(f"🌍 **{fuseau}**")
    date_depart = datetime.combine(date_dep, heure_dep)

    with etapes.container():
        with st.spinner("🌅 Lever/coucher du soleil…"):
            infos_soleil = recuperer_soleil(
                points_gpx[0].latitude, points_gpx[0].longitude,
                date_dep.strftime("%Y-%m-%d"))

    # ── CALCULS PARCOURS ─────────────────────────────────────────────────────
    with etapes.container():
        with st.spinner("📐 Calcul du parcours…"):
            checkpoints = []; profil_data = []
            dist_tot = d_plus = d_moins = temps_s = prochain = cap = 0.0
            vms = (vitesse*1000)/3600
            for i in range(1, len(points_gpx)):
                p1, p2 = points_gpx[i-1], points_gpx[i]
                d  = p1.distance_2d(p2) or 0.0; dp = 0.0
                if p1.elevation is not None and p2.elevation is not None:
                    dif = p2.elevation - p1.elevation
                    if dif>0: dp=dif; d_plus+=dif
                    else: d_moins+=abs(dif)
                dist_tot += d; temps_s += (d+dp*10)/vms
                cap = calculer_cap(p1.latitude,p1.longitude,p2.latitude,p2.longitude)
                profil_data.append({"Distance (km)":round(dist_tot/1000,3),"Altitude (m)":p2.elevation or 0})
                if temps_s >= prochain:
                    hp = date_depart+timedelta(seconds=temps_s)
                    checkpoints.append({
                        "lat":p2.latitude,"lon":p2.longitude,"Cap":cap,
                        "Heure":hp.strftime("%d/%m %H:%M"),
                        "Heure_API":hp.replace(minute=0,second=0).strftime("%Y-%m-%dT%H:00"),
                        "Km":round(dist_tot/1000,1),"Alt (m)":int(p2.elevation) if p2.elevation else 0,
                    })
                    prochain += intervalle_sec

    heure_arr = date_depart+timedelta(seconds=temps_s)
    pf = points_gpx[-1]
    checkpoints.append({
        "lat":pf.latitude,"lon":pf.longitude,"Cap":cap,
        "Heure":heure_arr.strftime("%d/%m %H:%M")+" 🏁",
        "Heure_API":heure_arr.replace(minute=0,second=0).strftime("%Y-%m-%dT%H:00"),
        "Km":round(dist_tot/1000,1),"Alt (m)":int(pf.elevation) if pf.elevation else 0,
    })
    df_profil = pd.DataFrame(profil_data)

    with etapes.container():
        with st.spinner("⛰️ Détection des ascensions…"):
            ascensions = detecter_ascensions(df_profil)

    with etapes.container():
        with st.spinner("📡 Récupération météo…"):
            frozen   = tuple((cp["lat"],cp["lon"],cp["Heure_API"]) for cp in checkpoints)
            rep_list = recuperer_meteo_batch(frozen)

    etapes.empty()

    resultats = []; err_meteo = rep_list is None
    if err_meteo:
        st.warning("⚠️ Météo indisponible.")
        for cp in checkpoints:
            cp.update(Ciel="—",temp_val=None,Pluie="—",pluie_pct=None,
                      vent_val=None,rafales_val=None,Dir="—",dir_deg=None,effet="—",ressenti=None)
            resultats.append(cp)
    else:
        for i,cp in enumerate(checkpoints):
            m = extraire_meteo(rep_list[i] if i<len(rep_list) else {}, cp["Heure_API"])
            if m["dir_deg"] is not None: m["effet"] = direction_vent_relative(cp["Cap"],m["dir_deg"])
            cp.update(m); resultats.append(cp)

    # ── SCORE GLOBAL ──────────────────────────────────────────────────────────
    score = calculer_score(resultats, ascensions, d_plus, vitesse, ref_val, mode, poids)
    st.markdown(f"""
    <div class="score-card">
      <div>
        <div class="score-note">{score['total']}<span style="font-size:1.4rem">/10</span></div>
        <div class="score-label">{score['label']}</div>
        <div class="score-sub">Score global de la sortie</div>
      </div>
      <div class="score-pills">
        <div class="pill">🌤️ Météo &nbsp;<b>{score['score_meteo']}/4</b></div>
        <div class="pill">⛰️ Cols &nbsp;<b>{score['score_cols']}/3</b></div>
        <div class="pill">⚡ Effort &nbsp;<b>{score['score_effort']}/3</b></div>
      </div>
    </div>""", unsafe_allow_html=True)

    # ── ONGLETS ───────────────────────────────────────────────────────────────
    tab_carte, tab_profil, tab_meteo, tab_cols, tab_detail = st.tabs([
        "🗺️ Carte", "⛰️ Profil & Cols", "🌤️ Météo", "🏔️ Ascensions", "📋 Détail"
    ])

    # ── CARTE ────────────────────────────────────────────────────────────────
    with tab_carte:
        dh = int(temps_s//3600); dm = int((temps_s%3600)//60)
        st.markdown(f"""
        <div class="metric-grid">
          <div class="metric-card"><div class="val">{round(dist_tot/1000,1)} km</div><div class="lbl">📏 Distance</div></div>
          <div class="metric-card"><div class="val">{int(d_plus)} m</div><div class="lbl">⬆️ Dénivelé +</div></div>
          <div class="metric-card"><div class="val">{int(d_moins)} m</div><div class="lbl">⬇️ Dénivelé −</div></div>
          <div class="metric-card"><div class="val">{dh}h{dm:02d}m</div><div class="lbl">⏱️ Durée estimée</div></div>
          <div class="metric-card"><div class="val">{heure_arr.strftime('%H:%M')}</div><div class="lbl">🏁 Arrivée</div></div>
          <div class="metric-card"><div class="val">{len(ascensions)}</div><div class="lbl">🏔️ Cols détectés</div></div>
        </div>""", unsafe_allow_html=True)

        if infos_soleil:
            ls = infos_soleil["lever"].strftime("%H:%M")
            cs = infos_soleil["coucher"].strftime("%H:%M")
            ds = infos_soleil["coucher"] - infos_soleil["lever"]
            hj, mj = int(ds.seconds//3600), int((ds.seconds%3600)//60)
            st.markdown(f"""
            <div class="soleil-row">
              <span style="font-size:1.3rem">☀️</span>
              <div class="soleil-item"><div class="s-val">🌅 {ls}</div><div class="s-lbl">Lever (UTC)</div></div>
              <div class="soleil-item"><div class="s-val">🌇 {cs}</div><div class="s-lbl">Coucher (UTC)</div></div>
              <div class="soleil-item"><div class="s-val">{hj}h{mj:02d}m</div><div class="s-lbl">Durée du jour</div></div>
            </div>""", unsafe_allow_html=True)
            tz = infos_soleil["lever"].tzinfo
            if date_depart.replace(tzinfo=tz) < infos_soleil["lever"]:
                st.warning(f"⚠️ Départ avant le lever du soleil ({ls} UTC) — prévoyez un éclairage.")
            if heure_arr.replace(tzinfo=tz) > infos_soleil["coucher"]:
                st.warning(f"⚠️ Arrivée après le coucher ({cs} UTC) — prévoyez un éclairage.")

        carte = creer_carte(points_gpx, resultats)
        st_folium(carte, width="100%", height=700, returned_objects=[])

    # ── PROFIL ───────────────────────────────────────────────────────────────
    with tab_profil:
        lbl_mode = "FTP" if mode=="⚡ Puissance" else "FC max"
        st.caption(f"Segments colorés selon les zones {lbl_mode}. Survolez pour les détails.")
        idx_survol = None
        if ascensions:
            noms  = ["(toutes les côtes)"] + [
                f"{a['Catégorie']} — Km {a['Départ (km)']}→{a['Sommet (km)']} ({a['Longueur']})"
                for a in ascensions]
            choix = st.selectbox("🔍 Mettre en avant :", options=noms, index=0)
            if choix != "(toutes les côtes)":
                idx_survol = noms.index(choix) - 1
        if not df_profil.empty:
            st.plotly_chart(
                creer_figure_profil(df_profil, ascensions, vitesse, ref_val, mode, poids, idx_survol),
                use_container_width=True)
        st.markdown(f"**Zones d'entraînement ({lbl_mode}) :**")
        cols_z = st.columns(6)
        for j, (_, _, num, lbl, coul) in enumerate(zones_actives(mode)):
            cols_z[j].markdown(
                f'<div style="background:{coul};color:white;border-radius:6px;'
                f'padding:6px;text-align:center;font-size:.72rem"><b>{lbl}</b></div>',
                unsafe_allow_html=True)

    # ── MÉTÉO ────────────────────────────────────────────────────────────────
    with tab_meteo:
        if err_meteo:
            st.warning("⚠️ Données météo indisponibles.")
        else:
            st.caption("Température · Vent & Rafales · Probabilité de pluie. "
                       "Zoom disponible — double-clic pour réinitialiser la vue.")
            st.plotly_chart(creer_figure_meteo(resultats), use_container_width=True)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Température** — 🟣 <5° · 🔵 5–15° · 🟢 15–22° (idéal) · 🟠 22–30° · 🔴 >30°C")
            with c2:
                st.markdown("**Vent** — 🟢 <10 · 🟡 10–25 · 🟠 25–40 · 🔴 >40 km/h | "
                            "**Pluie** — clair→foncé : 0→100%")

    # ── ASCENSIONS ───────────────────────────────────────────────────────────
    with tab_cols:
        st.caption("**UCI** — Score = (D+ × pente moy.) / 100 · "
                   "🔵 4ème ≥2 · 🟢 3ème ≥8 · 🟡 2ème ≥20 · 🟠 1ère ≥40 · 🔴 HC ≥80")
        if ascensions:
            for a in ascensions:
                w     = estimer_watts(a["_pente_moy"], vitesse, poids)
                _, zlbl, _ = get_zone(w, ref_val, zones_actives(mode))
                pct   = round(w/ref_val*100) if ref_val>0 else 0
                a["Puissance"]  = f"{w} W"
                if mode == "⚡ Puissance":
                    a["Effort val"] = f"{pct}% FTP"
                else:
                    fc_est = estimer_fc(w, ref_val*0.85, ref_val)  # ftp estimé à 85% FCmax
                    a["Effort val"] = f"~{fc_est} bpm" if fc_est else "—"
                a["Zone"]   = zlbl
                a["Effort"] = ("🔴 Max" if pct>105 else "🟠 Très dur" if pct>95
                               else "🟡 Difficile" if pct>80 else "🟢 Modéré" if pct>60
                               else "🔵 Endurance")
            cols_aff = ["Catégorie","Départ (km)","Sommet (km)","Longueur",
                        "Dénivelé","Pente moy.","Pente max","Alt. sommet",
                        "Score UCI","Puissance","Effort val","Zone","Effort"]
            st.dataframe(pd.DataFrame(ascensions)[cols_aff],
                use_container_width=True, hide_index=True,
                column_config={
                    "Effort val": st.column_config.TextColumn(
                        "% FTP" if mode=="⚡ Puissance" else "FC estimée",
                        help="Pourcentage FTP ou FC estimée selon le mode sélectionné"),
                    "Zone":   st.column_config.TextColumn("Zone",   help="Zone d'entraînement"),
                    "Effort": st.column_config.TextColumn("Effort", help="Intensité estimée"),
                })
        else:
            st.success("🚴‍♂️ Aucune difficulté catégorisée — parcours roulant !")

    # ── DÉTAIL MÉTÉO ─────────────────────────────────────────────────────────
    with tab_detail:
        st.caption(f"Un point toutes les **{intervalle} min**. "
                   "Wind Chill affiché si temp ≤ 10°C et vent > 4.8 km/h.")
        lignes = []
        for cp in resultats:
            t = cp.get("temp_val")
            lignes.append({
                "Heure":cp["Heure"],"Km":cp["Km"],"Alt (m)":cp["Alt (m)"],
                "Ciel":cp.get("Ciel","—"),
                "Temp (°C)":f"{t}°C" if t is not None else "—",
                "Ressenti":label_wind_chill(cp.get("ressenti")),
                "Pluie":cp.get("Pluie","—"),
                "Vent (km/h)":cp.get("vent_val") or "—",
                "Rafales":cp.get("rafales_val") or "—",
                "Direction":cp.get("Dir","—"),
                "Effet vent":cp.get("effet","—"),
            })
        st.dataframe(pd.DataFrame(lignes), use_container_width=True, hide_index=True,
            column_config={
                "Heure":       st.column_config.TextColumn("🕐 Heure",    help="Heure estimée de passage"),
                "Km":          st.column_config.NumberColumn("📏 Km",      help="Distance depuis le départ"),
                "Alt (m)":     st.column_config.NumberColumn("⛰️ Alt",     help="Altitude"),
                "Ciel":        st.column_config.TextColumn("🌤️ Ciel",     help="Conditions générales"),
                "Temp (°C)":   st.column_config.TextColumn("🌡️ Temp",     help="Température à 2m"),
                "Ressenti":    st.column_config.TextColumn("🥶 Ressenti",  help="Wind Chill NOAA"),
                "Pluie":       st.column_config.TextColumn("🌧️ Pluie",    help="Probabilité de pluie"),
                "Vent (km/h)": st.column_config.TextColumn("💨 Vent",      help="Vent moyen à 10m"),
                "Rafales":     st.column_config.TextColumn("🌬️ Rafales",  help="Vitesse des rafales"),
                "Direction":   st.column_config.TextColumn("🧭 Direction", help="Direction d'où vient le vent"),
                "Effet vent":  st.column_config.TextColumn("🚴 Effet",     help="Ressenti selon le cap"),
            })


if __name__ == "__main__":
    main()    flex-wrap: wrap;
  }
  .score-note  { font-size: 3.5rem; font-weight: 900; line-height: 1; }
  .score-label { font-size: 1.1rem; font-weight: 600; margin-top: 4px; opacity: 0.95; }
  .score-sub   { font-size: 0.82rem; opacity: 0.75; margin-top: 2px; }
  .score-pills { display: flex; gap: 10px; flex-wrap: wrap; }
  .pill {
    background: rgba(255,255,255,0.15);
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 0.82rem;
    backdrop-filter: blur(4px);
  }

  /* Badges météo */
  .badge-ok    { background:#dcfce7; color:#15803d; border-radius:6px; padding:2px 8px; font-size:0.78rem; }
  .badge-warn  { background:#fef9c3; color:#a16207; border-radius:6px; padding:2px 8px; font-size:0.78rem; }
  .badge-bad   { background:#fee2e2; color:#b91c1c; border-radius:6px; padding:2px 8px; font-size:0.78rem; }

  /* Soleil */
  .soleil-row {
    display: flex; gap: 16px; flex-wrap: wrap;
    background: linear-gradient(90deg, #fef3c7, #fde68a);
    border-radius: var(--radius);
    padding: 14px 20px;
    margin: 12px 0;
    align-items: center;
  }
  .soleil-item { text-align: center; }
  .soleil-item .s-val { font-size: 1.1rem; font-weight: 700; color: #92400e; }
  .soleil-item .s-lbl { font-size: 0.72rem; color: #b45309; }

  /* Responsive */
  @media (max-width: 640px) {
    .app-header h1 { font-size: 1.4rem; }
    .score-card    { flex-direction: column; gap: 14px; }
    .score-note    { font-size: 2.5rem; }
  }
</style>
"""

# ==============================================================================
# SECTION 1 : FONCTIONS UTILITAIRES
# ==============================================================================

def calculer_cap(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def direction_vent_relative(cap_velo, dir_vent):
    diff = (dir_vent - cap_velo) % 360
    if diff <= 45 or diff >= 315:   return "⬇️ Face"
    elif 135 <= diff <= 225:        return "⬆️ Dos"
    elif 45 < diff < 135:           return "↘️ Côté (D)"
    else:                           return "↙️ Côté (G)"


def obtenir_icone_meteo(code):
    mapping = {
        0:"☀️ Clair", 1:"⛅ Éclaircies", 2:"⛅ Éclaircies", 3:"☁️ Couvert",
        45:"🌫️ Brouillard", 48:"🌫️ Brouillard",
        51:"🌦️ Bruine", 53:"🌦️ Bruine", 55:"🌦️ Bruine",
        61:"🌧️ Pluie", 63:"🌧️ Pluie", 65:"🌧️ Pluie",
        66:"🌧️ Pluie", 67:"🌧️ Pluie", 80:"🌧️ Pluie", 81:"🌧️ Pluie", 82:"🌧️ Pluie",
        71:"❄️ Neige", 73:"❄️ Neige", 75:"❄️ Neige", 77:"❄️ Neige",
        85:"❄️ Neige", 86:"❄️ Neige",
        95:"⛈️ Orage", 96:"⛈️ Orage", 99:"⛈️ Orage",
    }
    return mapping.get(code, "❓ Inconnu")


def wind_chill(temp_c, vent_kmh):
    """Formule NOAA — valide si temp <= 10°C et vent > 4.8 km/h."""
    if temp_c > 10 or vent_kmh <= 4.8:
        return None
    wc = (13.12 + 0.6215 * temp_c
          - 11.37 * (vent_kmh ** 0.16)
          + 0.3965 * temp_c * (vent_kmh ** 0.16))
    return round(wc)


def label_wind_chill(ressenti):
    if ressenti is None: return "—"
    if ressenti <= -40:  return f"🟣 {ressenti}°C (Danger extrême)"
    if ressenti <= -27:  return f"🔴 {ressenti}°C (Très dangereux)"
    if ressenti <= -10:  return f"🟠 {ressenti}°C (Dangereux)"
    if ressenti <= 0:    return f"🟡 {ressenti}°C (Froid intense)"
    return               f"🔵 {ressenti}°C (Frais)"


def estimer_watts(pente_pct, vitesse_kmh, poids_total_kg=75):
    g = 9.81
    vitesse_ms = vitesse_kmh / 3.6
    pente_rad  = math.atan(pente_pct / 100)
    return max(0, int(
        poids_total_kg * g * math.sin(pente_rad) * vitesse_ms
        + poids_total_kg * g * 0.004 * vitesse_ms
    ))


def zone_ftp(watts, ftp):
    """Retourne la zone d'entraînement (1-6) selon le % FTP."""
    if ftp <= 0: return 1
    pct = watts / ftp
    if pct < 0.55:  return 1   # Récupération active
    if pct < 0.75:  return 2   # Endurance
    if pct < 0.90:  return 3   # Tempo
    if pct < 1.05:  return 4   # Seuil
    if pct < 1.20:  return 5   # VO2max
    return 6                   # Anaérobie

COULEURS_ZONES = {
    1: "#94a3b8",   # Gris — Récup
    2: "#3b82f6",   # Bleu — Endurance
    3: "#22c55e",   # Vert — Tempo
    4: "#eab308",   # Jaune — Seuil
    5: "#f97316",   # Orange — VO2max
    6: "#ef4444",   # Rouge — Anaérobie
}
LABELS_ZONES = {
    1: "Z1 Récup", 2: "Z2 Endurance", 3: "Z3 Tempo",
    4: "Z4 Seuil", 5: "Z5 VO2max",   6: "Z6 Anaérobie",
}


# ==============================================================================
# SECTION 2 : CATÉGORISATION UCI OFFICIELLE
# ==============================================================================

SEUILS_UCI = {
    "🔴 HC":        80,
    "🟠 1ère Cat.": 40,
    "🟡 2ème Cat.": 20,
    "🟢 3ème Cat.":  8,
    "🔵 4ème Cat.":  2,
}

def categoriser_ascension_uci(distance_m, d_plus):
    if distance_m < 300 or d_plus < 10:
        return None, 0
    pente_moy = (d_plus / distance_m) * 100
    if pente_moy < 2.0:
        return None, 0
    score = (d_plus * pente_moy) / 100
    for label, seuil in SEUILS_UCI.items():
        if score >= seuil:
            return label, round(score, 2)
    return None, 0


# ==============================================================================
# SECTION 3 : DÉTECTION DES ASCENSIONS
# ==============================================================================

LISSAGE_FENETRE = 5
SEUIL_DEBUT_M   = 10
MARGE_FIN_M     = 30
FUSION_MAX_M    = 25


def lisser_altitude(alts, fenetre=5):
    demi, n, result = fenetre // 2, len(alts), []
    for i in range(n):
        s, e = max(0, i-demi), min(n, i+demi+1)
        result.append(sum(alts[s:e]) / (e-s))
    return result


def detecter_segments_montants(dists, alts_lisses):
    segments, n = [], len(alts_lisses)
    en_montee = False
    creux_idx = sommet_idx = 0
    for i in range(1, n):
        alt = alts_lisses[i]
        if not en_montee:
            if alt < alts_lisses[creux_idx]: creux_idx = i
            elif alt >= alts_lisses[creux_idx] + SEUIL_DEBUT_M:
                en_montee, sommet_idx = True, i
        else:
            if alt > alts_lisses[sommet_idx]: sommet_idx = i
            elif alt <= alts_lisses[sommet_idx] - MARGE_FIN_M:
                segments.append((creux_idx, sommet_idx))
                en_montee, creux_idx, sommet_idx = False, i, i
    if en_montee and sommet_idx > creux_idx:
        segments.append((creux_idx, sommet_idx))
    return segments


def fusionner_segments(segments, alts_lisses):
    if not segments: return []
    fusionnes = [segments[0]]
    for debut, sommet in segments[1:]:
        pd_, ps_ = fusionnes[-1]
        descente = alts_lisses[ps_] - alts_lisses[debut]
        if descente <= FUSION_MAX_M:
            ns = sommet if alts_lisses[sommet] >= alts_lisses[ps_] else ps_
            fusionnes[-1] = (pd_, ns)
        else:
            fusionnes.append((debut, sommet))
    return fusionnes


def calculer_pente_max(dists, alts, debut_idx, sommet_idx, fenetre_km=0.05):
    pente_max = 0.0
    for i in range(debut_idx + 1, sommet_idx + 1):
        for j in range(i-1, max(debut_idx-1, i-50), -1):
            dist_diff = (dists[i] - dists[j]) * 1000
            if dist_diff >= fenetre_km * 1000:
                pente = ((alts[i] - alts[j]) / dist_diff) * 100
                if 0 < pente <= 40: pente_max = max(pente_max, pente)
                break
    return round(pente_max, 1)


def detecter_ascensions(df_profil):
    if df_profil.empty or len(df_profil) < 3: return []
    alts  = df_profil["Altitude (m)"].tolist()
    dists = df_profil["Distance (km)"].tolist()
    alts_lisses = lisser_altitude(alts, LISSAGE_FENETRE)
    segments    = fusionner_segments(detecter_segments_montants(dists, alts_lisses), alts_lisses)

    ascensions = []
    for debut_idx, sommet_idx in segments:
        d0, d1    = dists[debut_idx], dists[sommet_idx]
        a0, a1    = alts[debut_idx],  alts[sommet_idx]
        dist_km   = d1 - d0
        d_plus    = a1 - a0
        if dist_km <= 0 or d_plus <= 0: continue
        cat, score = categoriser_ascension_uci(dist_km * 1000, d_plus)
        if cat is None: continue
        pente_moy  = (d_plus / (dist_km * 1000)) * 100
        pente_max  = calculer_pente_max(dists, alts, debut_idx, sommet_idx)
        ascensions.append({
            "Catégorie":   cat,
            "Départ (km)": round(d0, 1),
            "Sommet (km)": round(d1, 1),
            "Longueur":    f"{round(dist_km, 1)} km",
            "Dénivelé":    f"{int(d_plus)} m",
            "Pente moy.":  f"{round(pente_moy, 1)} %",
            "Pente max":   f"{round(pente_max, 1)} %",
            "Alt. sommet": f"{int(a1)} m",
            "Score UCI":   score,
            "_debut_km":   d0,
            "_sommet_km":  d1,
            "_pente_moy":  pente_moy,
        })
    ascensions.sort(key=lambda x: x["_debut_km"])
    return ascensions


# ==============================================================================
# SECTION 4 : CACHE & API
# ==============================================================================

@st.cache_data(show_spinner=False)
def parser_gpx(contenu):
    try:
        gpx = gpxpy.parse(contenu)
        return [p for t in gpx.tracks for s in t.segments for p in s.points]
    except Exception as e:
        logger.error(f"Erreur GPX : {e}"); return []


@st.cache_data(show_spinner=False)
def recuperer_fuseau(lat, lon):
    try:
        r = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m&timezone=auto", timeout=10)
        r.raise_for_status()
        return r.json().get("timezone", "UTC")
    except Exception as e:
        logger.warning(f"Fuseau : {e}"); return "UTC"


@st.cache_data(ttl=1800, show_spinner=False)
def recuperer_meteo_batch(checkpoints_frozen):
    if not checkpoints_frozen: return []
    lats = ",".join(str(c[0]) for c in checkpoints_frozen)
    lons = ",".join(str(c[1]) for c in checkpoints_frozen)
    url  = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&hourly=temperature_2m,precipitation_probability,weathercode,"
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m&timezone=auto"
    )
    try:
        r = requests.get(url, timeout=15); r.raise_for_status()
        d = r.json(); return d if isinstance(d, list) else [d]
    except requests.exceptions.Timeout:
        logger.error("Timeout météo"); return None
    except Exception as e:
        logger.error(f"Météo : {e}"); return None


@st.cache_data(show_spinner=False)
def recuperer_soleil(lat, lon, date_str):
    try:
        r = requests.get(
            f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}"
            f"&date={date_str}&formatted=0", timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "OK": return None
        return {
            "lever":   datetime.fromisoformat(data["results"]["sunrise"]),
            "coucher": datetime.fromisoformat(data["results"]["sunset"]),
        }
    except Exception as e:
        logger.warning(f"Soleil : {e}"); return None


def extraire_meteo(donnees_api, heure_api):
    vide = dict(Ciel="—", temp_val=None, Pluie="—", pluie_pct=None,
                vent_val=None, rafales_val=None, Dir="—",
                dir_deg=None, effet="—", ressenti=None)
    if not donnees_api or "hourly" not in donnees_api: return vide
    heures = donnees_api["hourly"].get("time", [])
    if heure_api not in heures: return vide
    idx = heures.index(heure_api)
    h   = donnees_api["hourly"]
    def sg(k, d=None):
        v = h.get(k, []); return v[idx] if idx < len(v) else d
    dir_deg   = sg("wind_direction_10m")
    dirs      = ["N","NE","E","SE","S","SO","O","NO"]
    dir_label = dirs[round(dir_deg/45)%8] if dir_deg is not None else "—"
    temp      = sg("temperature_2m")
    vent      = sg("wind_speed_10m")
    pluie_raw = sg("precipitation_probability")
    try:    pluie_pct = int(pluie_raw)
    except: pluie_pct = None
    return {
        "Ciel":        obtenir_icone_meteo(sg("weathercode", 0)),
        "temp_val":    temp,
        "Pluie":       f"{pluie_pct}%" if pluie_pct is not None else "—",
        "pluie_pct":   pluie_pct,
        "vent_val":    vent,
        "rafales_val": sg("wind_gusts_10m"),
        "Dir":         dir_label,
        "dir_deg":     dir_deg,
        "effet":       "—",
        "ressenti":    wind_chill(temp, vent) if (temp is not None and vent is not None) else None,
    }


# ==============================================================================
# SECTION 5 : SCORE GLOBAL DE SORTIE
# ==============================================================================

def calculer_score_sortie(resultats_meteo, ascensions, d_plus_tot, vitesse, ftp, poids):
    """
    Score global /10 composé de 3 sous-scores :
    - Météo (4pts)  : température, vent, pluie
    - Cols  (3pts)  : dénivelé total et catégories
    - Effort (3pts) : puissance moyenne estimée vs FTP
    """
    # ── Météo ──
    temps_valides = [cp for cp in resultats_meteo if cp.get("temp_val") is not None]
    score_meteo   = 4.0

    if temps_valides:
        temp_moy  = sum(cp["temp_val"] for cp in temps_valides) / len(temps_valides)
        vent_moy  = sum(cp.get("vent_val") or 0 for cp in temps_valides) / len(temps_valides)
        pluie_moy = sum(cp.get("pluie_pct") or 0 for cp in temps_valides) / len(temps_valides)

        # Temp idéale : 15-22°C
        if 15 <= temp_moy <= 22:   score_meteo -= 0
        elif 10 <= temp_moy <= 28: score_meteo -= 0.5
        elif 5  <= temp_moy <= 32: score_meteo -= 1.5
        else:                      score_meteo -= 2.5

        # Vent
        if vent_moy > 40:  score_meteo -= 1.5
        elif vent_moy > 25: score_meteo -= 1.0
        elif vent_moy > 15: score_meteo -= 0.5

        # Pluie
        if pluie_moy > 70:  score_meteo -= 1.5
        elif pluie_moy > 40: score_meteo -= 1.0
        elif pluie_moy > 20: score_meteo -= 0.3
    else:
        score_meteo = 2.0  # inconnue → neutre

    # ── Cols ──
    score_cols = 3.0
    if d_plus_tot < 500:    score_cols = 3.0
    elif d_plus_tot < 1500: score_cols = 2.0
    elif d_plus_tot < 3000: score_cols = 1.0
    else:                   score_cols = 0.5

    # Bonus/malus sur les catégories
    cats = [a["Catégorie"] for a in ascensions]
    bonus_cols = (
        cats.count("🔴 HC")        * -0.5 +
        cats.count("🟠 1ère Cat.") * -0.3 +
        cats.count("🟡 2ème Cat.") * -0.1
    )
    score_cols = max(0, score_cols + bonus_cols)

    # ── Effort ──
    score_effort = 3.0
    if ascensions and ftp > 0:
        watts_list = [
            estimer_watts(a["_pente_moy"], vitesse, poids)
            for a in ascensions
        ]
        w_moy = sum(watts_list) / len(watts_list)
        pct   = w_moy / ftp
        if pct > 1.10:  score_effort = 0.5
        elif pct > 0.95: score_effort = 1.0
        elif pct > 0.80: score_effort = 1.5
        elif pct > 0.60: score_effort = 2.5
        else:            score_effort = 3.0

    total = round(min(10, max(0, score_meteo + score_cols + score_effort)), 1)

    label = (
        "🔴 Sortie très difficile"  if total < 4  else
        "🟠 Sortie difficile"       if total < 6  else
        "🟡 Sortie engagée"         if total < 7.5 else
        "🟢 Bonne sortie"           if total < 9  else
        "⭐ Sortie idéale"
    )

    return {
        "total":         total,
        "label":         label,
        "score_meteo":   round(max(0, score_meteo), 1),
        "score_cols":    round(score_cols, 1),
        "score_effort":  round(score_effort, 1),
    }


# ==============================================================================
# SECTION 6 : GRAPHIQUES PLOTLY
# ==============================================================================

COULEURS_CAT = {
    "🔴 HC":          "#ef4444",
    "🟠 1ère Cat.":   "#f97316",
    "🟡 2ème Cat.":   "#eab308",
    "🟢 3ème Cat.":   "#22c55e",
    "🔵 4ème Cat.":   "#3b82f6",
}


def creer_figure_profil(df_profil, ascensions, vitesse, ftp, poids, idx_survol=None):
    """Profil altimétrique avec overlay zones FTP sur les montées."""
    fig  = go.Figure()
    dists = df_profil["Distance (km)"].tolist()
    alts  = df_profil["Altitude (m)"].tolist()

    # Tracé de base
    fig.add_trace(go.Scatter(
        x=dists, y=alts,
        fill="tozeroy", fillcolor="rgba(59,130,246,0.12)",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="<b>Km %{x:.1f}</b><br>Altitude : %{y:.0f} m<extra></extra>",
        name="Profil",
    ))

    # Segments colorés par ascension + zone FTP
    for i, asc in enumerate(ascensions):
        d0, d1   = asc["_debut_km"], asc["_sommet_km"]
        cat      = asc["Catégorie"]
        couleur  = COULEURS_CAT.get(cat, "#94a3b8")
        opacite  = 1.0 if idx_survol is None or idx_survol == i else 0.2

        seg_x = [d for d in dists if d0 <= d <= d1]
        seg_y = [alts[j] for j, d in enumerate(dists) if d0 <= d <= d1]
        if not seg_x: continue

        # Couleur fill = zone FTP
        w   = estimer_watts(asc["_pente_moy"], vitesse, poids)
        z   = zone_ftp(w, ftp)
        czf = COULEURS_ZONES[z]

        r, g, b = int(czf[1:3],16), int(czf[3:5],16), int(czf[5:7],16)

        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y,
            fill="tozeroy",
            fillcolor=f"rgba({r},{g},{b},{round(opacite*0.35,2)})",
            line=dict(color=couleur, width=3 if idx_survol==i else 2),
            opacity=opacite,
            hovertemplate=(
                f"<b>{cat}</b> — {LABELS_ZONES[z]}<br>"
                f"Km %{{x:.1f}}<br>Alt : %{{y:.0f}} m<br>"
                f"Puissance est. : {w} W ({round(w/ftp*100) if ftp>0 else '?'}% FTP)"
                "<extra></extra>"
            ),
            name=f"{cat} · {LABELS_ZONES[z]}",
        ))

        fig.add_annotation(
            x=d1, y=seg_y[-1] if seg_y else 0,
            text=f"▲ {cat.split()[0]}",
            showarrow=True, arrowhead=2, arrowsize=0.8,
            arrowcolor=couleur, font=dict(size=10, color=couleur),
            bgcolor="white", bordercolor=couleur, borderwidth=1,
            opacity=opacite,
        )

    fig.update_layout(
        height=360,
        margin=dict(l=50, r=20, t=30, b=40),
        xaxis=dict(title="Distance (km)", showgrid=True, gridcolor="#f1f5f9"),
        yaxis=dict(title="Altitude (m)",  showgrid=True, gridcolor="#f1f5f9"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


def creer_figure_meteo(resultats_meteo):
    """
    Graphique 3-en-1 : température + vent + pluie sur le même axe X.
    Température = ligne, vent = barres, pluie = aire.
    """
    kms, temps, vents, rafales, pluies = [], [], [], [], []
    couleurs_vent = []

    for cp in resultats_meteo:
        t = cp.get("temp_val")
        v = cp.get("vent_val")
        if t is None or v is None: continue
        kms.append(cp["Km"])
        temps.append(t)
        vents.append(v)
        rafales.append(cp.get("rafales_val") or v)
        pluies.append(cp.get("pluie_pct") or 0)
        if v >= 40:    couleurs_vent.append("#ef4444")
        elif v >= 25:  couleurs_vent.append("#f97316")
        elif v >= 10:  couleurs_vent.append("#eab308")
        else:          couleurs_vent.append("#22c55e")

    from plotly.subplots import make_subplots
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.45, 0.30, 0.25],
        vertical_spacing=0.04,
        subplot_titles=("🌡️ Température (°C)", "💨 Vent (km/h)", "🌧️ Probabilité pluie (%)"),
    )

    if kms:
        # Température
        couleurs_temp = []
        for t in temps:
            if t >= 30:    couleurs_temp.append("#ef4444")
            elif t >= 22:  couleurs_temp.append("#f97316")
            elif t >= 15:  couleurs_temp.append("#22c55e")
            elif t >= 5:   couleurs_temp.append("#3b82f6")
            else:          couleurs_temp.append("#8b5cf6")

        fig.add_trace(go.Scatter(
            x=kms, y=temps,
            mode="lines+markers",
            line=dict(color="#f97316", width=2.5),
            marker=dict(color=couleurs_temp, size=8, line=dict(color="white", width=1.5)),
            fill="tonexty",
            hovertemplate="<b>Km %{x}</b><br>Température : %{y}°C<extra></extra>",
            name="Température",
        ), row=1, col=1)

        # Zone de confort
        fig.add_hrect(y0=15, y1=22, row=1, col=1,
                      fillcolor="rgba(34,197,94,0.08)",
                      line_width=0, annotation_text="Zone idéale",
                      annotation_font_size=9, annotation_font_color="#16a34a")

        # Vent
        fig.add_trace(go.Bar(
            x=kms, y=vents,
            marker_color=couleurs_vent, name="Vent moyen",
            hovertemplate="<b>Km %{x}</b><br>Vent : %{y} km/h<extra></extra>",
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=kms, y=rafales,
            mode="lines+markers",
            line=dict(color="#94a3b8", width=1.5, dash="dot"),
            marker=dict(size=4),
            name="Rafales",
            hovertemplate="<b>Km %{x}</b><br>Rafales : %{y} km/h<extra></extra>",
        ), row=2, col=1)

        # Pluie
        couleurs_pluie = []
        for p in pluies:
            if p >= 70:    couleurs_pluie.append("#1d4ed8")
            elif p >= 40:  couleurs_pluie.append("#3b82f6")
            elif p >= 20:  couleurs_pluie.append("#93c5fd")
            else:          couleurs_pluie.append("#dbeafe")
        fig.add_trace(go.Bar(
            x=kms, y=pluies,
            marker_color=couleurs_pluie, name="Pluie",
            hovertemplate="<b>Km %{x}</b><br>Pluie : %{y}%<extra></extra>",
        ), row=3, col=1)
        fig.add_hline(y=50, row=3, col=1,
                      line_dash="dot", line_color="#94a3b8",
                      annotation_text="50%", annotation_font_size=9)

    fig.update_layout(
        height=560,
        margin=dict(l=50, r=20, t=40, b=40),
        hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        showlegend=False,
    )
    for i in range(1, 4):
        fig.update_xaxes(showgrid=True, gridcolor="#f1f5f9", row=i, col=1)
        fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", row=i, col=1)
    fig.update_xaxes(title_text="Distance (km)", row=3, col=1)
    return fig


# ==============================================================================
# SECTION 7 : CARTE FOLIUM
# ==============================================================================

def creer_carte(points_gpx, resultats_meteo):
    carte = folium.Map(
        location=[points_gpx[0].latitude, points_gpx[0].longitude],
        zoom_start=11, tiles="CartoDB positron",
        scrollWheelZoom=False,
    )
    folium.PolyLine(
        [[p.latitude, p.longitude] for p in points_gpx],
        color="#2563eb", weight=5, opacity=0.9,
    ).add_to(carte)
    folium.Marker([points_gpx[0].latitude,  points_gpx[0].longitude],
                  tooltip="🚦 Départ",
                  icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(carte)
    folium.Marker([points_gpx[-1].latitude, points_gpx[-1].longitude],
                  tooltip="🏁 Arrivée",
                  icon=folium.Icon(color="red",   icon="flag", prefix="fa")).add_to(carte)

    for cp in resultats_meteo:
        t = cp.get("temp_val")
        if t is None: continue

        dir_deg  = cp.get("dir_deg")
        vent_val = cp.get("vent_val", 0) or 0
        if vent_val >= 40:    fc = "#ef4444"
        elif vent_val >= 25:  fc = "#f97316"
        elif vent_val >= 10:  fc = "#eab308"
        else:                 fc = "#22c55e"

        fleche_svg = ""
        if dir_deg is not None:
            rot = (dir_deg + 180) % 360
            fleche_svg = (
                f'<svg width="16" height="16" viewBox="0 0 28 28" style="vertical-align:middle">'
                f'<g transform="rotate({rot},14,14)">'
                f'<polygon points="14,2 20,22 14,18 8,22" fill="{fc}"/>'
                f'</g></svg>'
            )

        pluie_pct = cp.get("pluie_pct")
        if pluie_pct is not None:
            if pluie_pct >= 70:   pc = "#1d4ed8"
            elif pluie_pct >= 40: pc = "#3b82f6"
            else:                 pc = "#93c5fd"
            barre_pluie = (
                f'<div style="margin:4px 0 2px;font-size:11px;color:#374151">'
                f'&#127783; Pluie : <b>{pluie_pct}%</b></div>'
                '<div style="background:#e5e7eb;border-radius:4px;height:6px;width:100%">'
                f'<div style="background:{pc};width:{pluie_pct}%;height:6px;border-radius:4px"></div></div>'
            )
        else:
            barre_pluie = '<div style="font-size:11px;color:#374151">&#127783; Pluie : —</div>'

        ressenti = cp.get("ressenti")

        popup_html = (
            '<div style="font-family:sans-serif;font-size:12px;min-width:200px">'
            '<div style="font-weight:700;font-size:13px;border-bottom:1px solid #e5e7eb;'
            'padding-bottom:4px;margin-bottom:6px">'
            f'{cp["Heure"]} — Km {cp["Km"]}</div>'
            f'<div style="color:#6b7280;margin-bottom:6px">⛰️ Alt : {cp["Alt (m)"]} m</div>'
            f'<div style="font-size:15px;margin-bottom:2px">{cp["Ciel"]} <b>{t}°C</b>'
            + (f' <span style="color:#6b7280;font-size:11px">(ressenti {ressenti}°C)</span>'
               if ressenti is not None else "")
            + '</div>'
            + barre_pluie
            + '<div style="margin-top:8px;padding-top:6px;border-top:1px solid #f3f4f6">'
            '<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">'
            f'{fleche_svg}'
            f'<span><b>{vent_val} km/h</b> <span style="color:#6b7280">du {cp["Dir"]}</span></span></div>'
            f'<div style="color:#6b7280;font-size:11px">Rafales : {cp.get("rafales_val","—")} km/h</div>'
            f'<div style="margin-top:4px;font-size:11px">🚴 <b>{cp.get("effet","—")}</b></div>'
            '</div></div>'
        )

        rot_str = str((dir_deg + 180) % 360) if dir_deg is not None else "0"
        tooltip_html = (
            f"{cp['Heure']} | {cp['Ciel']} {t}°C | "
            + (f'<svg width="12" height="12" viewBox="0 0 28 28" style="vertical-align:middle">'
               f'<g transform="rotate({rot_str},14,14)">'
               f'<polygon points="14,2 20,22 14,18 8,22" fill="{fc}"/>'
               f'</g></svg> ' if dir_deg is not None else "💨 ")
            + f"{vent_val} km/h"
        )

        folium.Marker(
            [cp["lat"], cp["lon"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=folium.Tooltip(tooltip_html, sticky=True),
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(carte)
    return carte


# ==============================================================================
# SECTION 8 : APPLICATION PRINCIPALE
# ==============================================================================

def main():
    st.set_page_config(page_title="Vélo & Météo", page_icon="🚴‍♂️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    # ── HEADER ───────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="app-header">
      <h1>🚴‍♂️ Vélo &amp; Météo</h1>
      <p>Analysez votre tracé GPX : météo en temps réel, cols UCI, profil interactif et zones d'entraînement.</p>
    </div>
    """, unsafe_allow_html=True)

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    st.sidebar.header("⚙️ Paramètres")
    date_dep  = st.sidebar.date_input("📅 Date de départ", value=date.today())
    heure_dep = st.sidebar.time_input("🕐 Heure de départ")
    vitesse   = st.sidebar.number_input("🚴 Vitesse moyenne plat (km/h)", 5, 60, 25)
    st.sidebar.divider()
    ftp   = st.sidebar.number_input("⚡ FTP (W)", 50, 500, 220,
              help="Puissance seuil — zones d'entraînement + effort sur les cols.")
    poids = st.sidebar.number_input("⚖️ Poids cycliste + vélo (kg)", 40, 150, 75)
    st.sidebar.divider()
    intervalle     = st.sidebar.selectbox("⏱️ Intervalle checkpoints météo",
                       options=[5,10,15], index=1,
                       format_func=lambda x: f"Toutes les {x} min")
    intervalle_sec = intervalle * 60

    # Légende zones FTP dans la sidebar
    st.sidebar.divider()
    st.sidebar.markdown("**Zones d'entraînement**")
    for z, lbl in LABELS_ZONES.items():
        st.sidebar.markdown(
            f'<span style="background:{COULEURS_ZONES[z]};color:white;'
            f'border-radius:4px;padding:2px 8px;font-size:0.78rem">{lbl}</span>',
            unsafe_allow_html=True
        )

    ph_fuseau = st.sidebar.empty()
    ph_fuseau.info("🌍 Fuseau : en attente…")

    # ── IMPORT GPX ────────────────────────────────────────────────────────────
    fichier = st.file_uploader("📂 Importez votre fichier parcours (.gpx)", type=["gpx"])
    if fichier is None:
        st.info("👆 Importez un fichier GPX pour commencer l'analyse.")
        return

    # ── CHARGEMENT PROGRESSIF ─────────────────────────────────────────────────
    etapes = st.empty()

    with etapes.container():
        with st.spinner("📍 Lecture du fichier GPX…"):
            points_gpx = parser_gpx(fichier.read())

    if not points_gpx:
        st.error("❌ Fichier GPX vide ou corrompu.")
        return

    with etapes.container():
        with st.spinner("🌍 Détection du fuseau horaire…"):
            fuseau = recuperer_fuseau(points_gpx[0].latitude, points_gpx[0].longitude)

    ph_fuseau.success(f"🌍 **{fuseau}**")
    date_depart = datetime.combine(date_dep, heure_dep)

    with etapes.container():
        with st.spinner("🌅 Récupération lever/coucher du soleil…"):
            infos_soleil = recuperer_soleil(
                points_gpx[0].latitude, points_gpx[0].longitude,
                date_dep.strftime("%Y-%m-%d")
            )

    # ── PHASE 1 : CALCULS PARCOURS ────────────────────────────────────────────
    with etapes.container():
        with st.spinner("📐 Calcul du parcours…"):
            checkpoints   = []
            profil_data   = []
            dist_tot_m    = d_plus_tot = d_moins_tot = temps_tot_sec = prochain_cp = 0.0
            cap_actuel    = 0.0
            vitesse_ms    = (vitesse * 1000) / 3600

            for i in range(1, len(points_gpx)):
                p1, p2  = points_gpx[i-1], points_gpx[i]
                dist    = p1.distance_2d(p2) or 0.0
                d_plus_local = 0.0
                if p1.elevation is not None and p2.elevation is not None:
                    diff = p2.elevation - p1.elevation
                    if diff > 0: d_plus_local = diff; d_plus_tot += diff
                    else: d_moins_tot += abs(diff)
                dist_tot_m    += dist
                temps_tot_sec += (dist + d_plus_local * 10) / vitesse_ms
                cap_actuel     = calculer_cap(p1.latitude, p1.longitude, p2.latitude, p2.longitude)
                profil_data.append({
                    "Distance (km)": round(dist_tot_m/1000, 3),
                    "Altitude (m)":  p2.elevation or 0,
                })
                if temps_tot_sec >= prochain_cp:
                    hp = date_depart + timedelta(seconds=temps_tot_sec)
                    checkpoints.append({
                        "lat": p2.latitude, "lon": p2.longitude, "Cap": cap_actuel,
                        "Heure":     hp.strftime("%d/%m %H:%M"),
                        "Heure_API": hp.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
                        "Km":        round(dist_tot_m/1000, 1),
                        "Alt (m)":   int(p2.elevation) if p2.elevation else 0,
                    })
                    prochain_cp += intervalle_sec

    heure_arr = date_depart + timedelta(seconds=temps_tot_sec)
    pf = points_gpx[-1]
    checkpoints.append({
        "lat": pf.latitude, "lon": pf.longitude, "Cap": cap_actuel,
        "Heure":     heure_arr.strftime("%d/%m %H:%M") + " 🏁",
        "Heure_API": heure_arr.replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00"),
        "Km":        round(dist_tot_m/1000, 1),
        "Alt (m)":   int(pf.elevation) if pf.elevation else 0,
    })
    df_profil = pd.DataFrame(profil_data)

    # ── PHASE 2 : ASCENSIONS ──────────────────────────────────────────────────
    with etapes.container():
        with st.spinner("⛰️ Détection des ascensions…"):
            ascensions = detecter_ascensions(df_profil)

    # ── PHASE 3 : MÉTÉO ───────────────────────────────────────────────────────
    with etapes.container():
        with st.spinner("📡 Récupération des données météo…"):
            frozen   = tuple((cp["lat"], cp["lon"], cp["Heure_API"]) for cp in checkpoints)
            rep_list = recuperer_meteo_batch(frozen)

    etapes.empty()

    resultats_meteo = []
    erreur_meteo = rep_list is None

    if erreur_meteo:
        st.warning("⚠️ Météo indisponible. Le reste de l'analyse est affiché.")
        for cp in checkpoints:
            cp.update(Ciel="—", temp_val=None, Pluie="—", pluie_pct=None,
                      vent_val=None, rafales_val=None, Dir="—",
                      dir_deg=None, effet="—", ressenti=None)
            resultats_meteo.append(cp)
    else:
        for i, cp in enumerate(checkpoints):
            m = extraire_meteo(rep_list[i] if i < len(rep_list) else {}, cp["Heure_API"])
            if m["dir_deg"] is not None:
                m["effet"] = direction_vent_relative(cp["Cap"], m["dir_deg"])
            cp.update(m)
            resultats_meteo.append(cp)

    # ── SCORE GLOBAL ──────────────────────────────────────────────────────────
    score = calculer_score_sortie(resultats_meteo, ascensions, d_plus_tot, vitesse, ftp, poids)

    st.markdown(f"""
    <div class="score-card">
      <div>
        <div class="score-note">{score['total']}<span style="font-size:1.5rem">/10</span></div>
        <div class="score-label">{score['label']}</div>
        <div class="score-sub">Score global de la sortie</div>
      </div>
      <div class="score-pills">
        <div class="pill">🌤️ Météo &nbsp;<b>{score['score_meteo']}/4</b></div>
        <div class="pill">⛰️ Cols &nbsp;<b>{score['score_cols']}/3</b></div>
        <div class="pill">⚡ Effort &nbsp;<b>{score['score_effort']}/3</b></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── ONGLETS ───────────────────────────────────────────────────────────────
    tab_carte, tab_profil, tab_meteo, tab_cols, tab_detail = st.tabs([
        "🗺️ Carte",
        "⛰️ Profil & Cols",
        "🌤️ Météo",
        "🏔️ Ascensions",
        "📋 Détail"
    ])

    # ════════════════════════════════════════════
    # ONGLET 1 : CARTE
    # ════════════════════════════════════════════
    with tab_carte:
        # Résumé métriques
        duree_h = int(temps_tot_sec // 3600)
        duree_m = int((temps_tot_sec % 3600) // 60)
        st.markdown(f"""
        <div class="metric-grid">
          <div class="metric-card"><div class="val">{round(dist_tot_m/1000,1)} km</div><div class="lbl">📏 Distance</div></div>
          <div class="metric-card"><div class="val">{int(d_plus_tot)} m</div><div class="lbl">⬆️ Dénivelé +</div></div>
          <div class="metric-card"><div class="val">{int(d_moins_tot)} m</div><div class="lbl">⬇️ Dénivelé −</div></div>
          <div class="metric-card"><div class="val">{duree_h}h{duree_m:02d}m</div><div class="lbl">⏱️ Durée estimée</div></div>
          <div class="metric-card"><div class="val">{heure_arr.strftime('%H:%M')}</div><div class="lbl">🏁 Arrivée estimée</div></div>
          <div class="metric-card"><div class="val">{len(ascensions)}</div><div class="lbl">🏔️ Cols détectés</div></div>
        </div>
        """, unsafe_allow_html=True)

        # Soleil
        if infos_soleil:
            lever_str   = infos_soleil["lever"].strftime("%H:%M")
            coucher_str = infos_soleil["coucher"].strftime("%H:%M")
            duree_s     = infos_soleil["coucher"] - infos_soleil["lever"]
            h_j = int(duree_s.seconds//3600)
            m_j = int((duree_s.seconds%3600)//60)
            st.markdown(f"""
            <div class="soleil-row">
              <span style="font-size:1.4rem">☀️</span>
              <div class="soleil-item"><div class="s-val">🌅 {lever_str}</div><div class="s-lbl">Lever (UTC)</div></div>
              <div class="soleil-item"><div class="s-val">🌇 {coucher_str}</div><div class="s-lbl">Coucher (UTC)</div></div>
              <div class="soleil-item"><div class="s-val">{h_j}h{m_j:02d}m</div><div class="s-lbl">Durée du jour</div></div>
            </div>
            """, unsafe_allow_html=True)

            dep_utc = date_depart.replace(tzinfo=infos_soleil["lever"].tzinfo)
            arr_utc = heure_arr.replace(tzinfo=infos_soleil["lever"].tzinfo)
            if dep_utc < infos_soleil["lever"]:
                st.warning(f"⚠️ Départ avant le lever du soleil ({lever_str} UTC) — prévoyez un éclairage.")
            if arr_utc > infos_soleil["coucher"]:
                st.warning(f"⚠️ Arrivée après le coucher ({coucher_str} UTC) — prévoyez un éclairage.")

        carte = creer_carte(points_gpx, resultats_meteo)
        st_folium(carte, width="100%", height=560, returned_objects=[])

    # ════════════════════════════════════════════
    # ONGLET 2 : PROFIL & COLS
    # ════════════════════════════════════════════
    with tab_profil:
        st.caption(
            "Survolez pour voir l'altitude. Les segments colorés correspondent aux zones FTP. "
            "Sélectionnez une côte pour la mettre en avant."
        )
        idx_survol = None
        if ascensions:
            noms = ["(toutes les côtes)"] + [
                f"{a['Catégorie']} — Km {a['Départ (km)']}→{a['Sommet (km)']} ({a['Longueur']})"
                for a in ascensions
            ]
            choix = st.selectbox("🔍 Mettre en avant :", options=noms, index=0)
            if choix != "(toutes les côtes)":
                idx_survol = noms.index(choix) - 1

        if not df_profil.empty:
            fig_profil = creer_figure_profil(df_profil, ascensions, vitesse, ftp, poids, idx_survol)
            st.plotly_chart(fig_profil, use_container_width=True)

        # Légende zones FTP sous le graphique
        st.markdown("**Zones d'entraînement sur les montées :**")
        cols_z = st.columns(6)
        for j, (z, lbl) in enumerate(LABELS_ZONES.items()):
            cols_z[j].markdown(
                f'<div style="background:{COULEURS_ZONES[z]};color:white;border-radius:6px;'
                f'padding:6px;text-align:center;font-size:0.75rem"><b>{lbl}</b></div>',
                unsafe_allow_html=True
            )

    # ════════════════════════════════════════════
    # ONGLET 3 : MÉTÉO
    # ════════════════════════════════════════════
    with tab_meteo:
        if erreur_meteo:
            st.warning("⚠️ Données météo indisponibles.")
        else:
            st.caption(
                "Graphique unifié Température / Vent / Pluie sur l'ensemble du parcours. "
                "La zone verte sur la température correspond à la plage idéale (15–22°C)."
            )
            fig_meteo = creer_figure_meteo(resultats_meteo)
            st.plotly_chart(fig_meteo, use_container_width=True)

            # Légendes couleurs
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Température**")
                st.markdown("🟣 < 5°C · 🔵 5–15°C · 🟢 15–22°C (idéal) · 🟠 22–30°C · 🔴 > 30°C")
            with c2:
                st.markdown("**Vent**")
                st.markdown("🟢 < 10 · 🟡 10–25 · 🟠 25–40 · 🔴 > 40 km/h")

    # ════════════════════════════════════════════
    # ONGLET 4 : ASCENSIONS
    # ════════════════════════════════════════════
    with tab_cols:
        st.caption(
            "**Catégorisation UCI officielle** — Score = (D+ × pente moy.) / 100. "
            "🔵 4ème ≥ 2 · 🟢 3ème ≥ 8 · 🟡 2ème ≥ 20 · 🟠 1ère ≥ 40 · 🔴 HC ≥ 80"
        )
        if ascensions:
            for a in ascensions:
                w   = estimer_watts(a["_pente_moy"], vitesse, poids)
                pct = round((w / ftp) * 100) if ftp > 0 else 0
                z   = zone_ftp(w, ftp)
                a["Puissance"]  = f"{w} W"
                a["% FTP"]      = f"{pct} %"
                a["Zone FTP"]   = LABELS_ZONES[z]
                a["Effort"]     = (
                    "🔴 Max"        if pct > 105 else
                    "🟠 Très dur"   if pct > 95  else
                    "🟡 Difficile"  if pct > 80  else
                    "🟢 Modéré"     if pct > 60  else
                    "🔵 Endurance"
                )
            cols_aff = [
                "Catégorie","Départ (km)","Sommet (km)","Longueur",
                "Dénivelé","Pente moy.","Pente max","Alt. sommet",
                "Score UCI","Puissance","% FTP","Zone FTP","Effort"
            ]
            st.dataframe(
                pd.DataFrame(ascensions)[cols_aff],
                use_container_width=True, hide_index=True,
                column_config={
                    "Catégorie":    st.column_config.TextColumn("Catégorie",   help="Classification UCI"),
                    "Départ (km)":  st.column_config.NumberColumn("Départ",    help="Km de début"),
                    "Sommet (km)":  st.column_config.NumberColumn("Sommet",    help="Km du sommet"),
                    "Longueur":     st.column_config.TextColumn("Longueur",    help="Distance totale"),
                    "Dénivelé":     st.column_config.TextColumn("D+",          help="Dénivelé positif"),
                    "Pente moy.":   st.column_config.TextColumn("Pente moy.",  help="Pente moyenne"),
                    "Pente max":    st.column_config.TextColumn("Pente max",   help="Pente max sur 50m"),
                    "Alt. sommet":  st.column_config.TextColumn("Alt. sommet", help="Altitude au sommet"),
                    "Score UCI":    st.column_config.NumberColumn("Score UCI",  help="(D+ × pente) / 100"),
                    "Puissance":    st.column_config.TextColumn("Puissance",   help=f"Watts estimés à {vitesse} km/h / {poids} kg"),
                    "% FTP":        st.column_config.TextColumn("% FTP",       help=f"% de votre FTP ({ftp} W)"),
                    "Zone FTP":     st.column_config.TextColumn("Zone",        help="Zone d'entraînement"),
                    "Effort":       st.column_config.TextColumn("Effort",      help="Intensité estimée"),
                }
            )
        else:
            st.success("🚴‍♂️ Aucune difficulté catégorisée — parcours roulant !")

    # ════════════════════════════════════════════
    # ONGLET 5 : DÉTAIL MÉTÉO
    # ════════════════════════════════════════════
    with tab_detail:
        st.caption(
            f"Un point toutes les **{intervalle} min**. "
            "**Effet Vent** : ⬇️ Face = freine · ⬆️ Dos = aide · ↘️↙️ Côté = déstabilise. "
            "**Ressenti** : Wind Chill NOAA (affiché si temp ≤ 10°C et vent > 4.8 km/h)."
        )
        lignes = []
        for cp in resultats_meteo:
            t = cp.get("temp_val")
            lignes.append({
                "Heure":       cp["Heure"],
                "Km":          cp["Km"],
                "Alt (m)":     cp["Alt (m)"],
                "Ciel":        cp.get("Ciel","—"),
                "Temp (°C)":   f"{t}°C" if t is not None else "—",
                "Ressenti":    label_wind_chill(cp.get("ressenti")),
                "Pluie":       cp.get("Pluie","—"),
                "Vent (km/h)": cp.get("vent_val") or "—",
                "Rafales":     cp.get("rafales_val") or "—",
                "Direction":   cp.get("Dir","—"),
                "Effet vent":  cp.get("effet","—"),
            })
        st.dataframe(
            pd.DataFrame(lignes),
            use_container_width=True, hide_index=True,
            column_config={
                "Heure":       st.column_config.TextColumn("🕐 Heure",     help="Heure de passage estimée"),
                "Km":          st.column_config.NumberColumn("📏 Km",       help="Distance depuis le départ"),
                "Alt (m)":     st.column_config.NumberColumn("⛰️ Alt",      help="Altitude"),
                "Ciel":        st.column_config.TextColumn("🌤️ Ciel",      help="Conditions générales"),
                "Temp (°C)":   st.column_config.TextColumn("🌡️ Temp",      help="Température à 2m"),
                "Ressenti":    st.column_config.TextColumn("🥶 Ressenti",   help="Wind Chill NOAA"),
                "Pluie":       st.column_config.TextColumn("🌧️ Pluie",     help="Probabilité de pluie"),
                "Vent (km/h)": st.column_config.TextColumn("💨 Vent",       help="Vent moyen à 10m"),
                "Rafales":     st.column_config.TextColumn("🌬️ Rafales",   help="Vitesse des rafales"),
                "Direction":   st.column_config.TextColumn("🧭 Direction",  help="Direction d'où vient le vent"),
                "Effet vent":  st.column_config.TextColumn("🚴 Effet",      help="Ressenti du vent selon le cap"),
            }
        )


if __name__ == "__main__":
    main()
