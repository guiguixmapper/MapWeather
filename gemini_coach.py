import google.generativeai as genai
import logging

logger = logging.getLogger(__name__)

def generer_briefing(api_key, dist_tot, d_plus, temps_s, calories, score, ascensions, analyse_meteo, resultats, heure_depart, heure_arrivee, vitesse_moyenne, infos_soleil, contexte_date, nb_points_eau, air_quality, is_past=False):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash') # Rapide et très doué en texte

    dist_km = round(dist_tot / 1000, 1)
    dh = int(temps_s // 3600); dm = int((temps_s % 3600) // 60)
    duree_str = f"{dh}h{dm:02d}"

    pluie_alerte = ""
    vent_alerte = ""
    if analyse_meteo:
        if analyse_meteo["pct_pluie"] > 20:
            pluie_alerte = f"- Risque de pluie sur {analyse_meteo['pct_pluie']}% du parcours. Premier risque au km {analyse_meteo['premier_pluie']['Km']}."
        if analyse_meteo["pct_face"] > 30:
            vent_alerte = f"- Attention : Vent de face sur {analyse_meteo['pct_face']}% du parcours."

    cols_str = ", ".join([f"{a.get('Nom', a['Catégorie'])} (D+{a['Dénivelé']})" for a in ascensions]) if ascensions else "Aucun col majeur."
    
    uv_str = f"Indice UV max : {air_quality['uv_max']}." if air_quality.get('uv_max') else ""
    pollen_str = f"Alerte Pollen : {air_quality['pollen_alerte']}." if air_quality.get('pollen_alerte') != "Aucune" else ""

    # Contexte pour l'IA (Si la date est passée, on lui dit d'analyser ce qui s'est passé, sinon de conseiller pour l'avenir)
    ton_temps = "C'est une sortie passée. Analyse les conditions qu'a affrontées le cycliste et la difficulté de l'exploit." if is_past else "C'est une sortie prévue. Agis comme un directeur sportif et donne tes conseils."

    prompt = f"""
Tu es un directeur sportif de cyclisme (type Marc Madiot), expert, bienveillant mais franc, avec un langage naturel de cycliste passionné.
{ton_temps}

Voici les données de la sortie ({contexte_date}) :
- Distance : {dist_km} km | Dénivelé + : {int(d_plus)} m | Vitesse moyenne réelle : {vitesse_moyenne} km/h
- Durée de roulage : {duree_str} | Dépense énergétique : {calories} kcal
- Heure départ : {heure_depart} | Heure arrivée : {heure_arrivee}
- Difficulté météo/parcours calculée : {score['total']}/10 ({score['label']})
- Ascensions : {cols_str}
- Points d'eau potable sur la route : {nb_points_eau}
- Qualité air : {uv_str} {pollen_str}
{pluie_alerte}
{vent_alerte}

Rédige un briefing fluide de 2 ou 3 paragraphes maximum (PAS de liste à puces basique).
Parle de la gestion de l'effort dans les cols, donne des conseils d'habillement précis selon la météo et la saison, gère son hydratation (s'il y a peu de points d'eau, alerte-le), et intègre les infos pollen/UV si pertinentes. Garde un ton amical et pro ("Salut champion", "Bonne route", etc.). Fais un texte qui donne envie d'y aller (ou qui félicite si la date est passée).
"""
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Erreur API Gemini : {e}")
        return None
