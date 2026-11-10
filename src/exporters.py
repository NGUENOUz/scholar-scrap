"""
Module exporters.py
-------------------
Génération des exports quotidiens pour les nouvelles bourses candidates :
- Fichier CSV (format utf-8-sig lisible directement dans Excel sans bug d'accent).
- Fichier Markdown structuré et lisible pour inspection rapide.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import List

from src.models import ScholarshipCandidate


def export_to_csv(candidates: List[ScholarshipCandidate], output_dir: str = "data") -> Path:
    """
    Exporte les candidats au format CSV du jour.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    file_path = out_path / f"candidates_{today_str}.csv"

    fieldnames = [
        "titre",
        "lien_officiel",
        "site_source",
        "url_decouverte",
        "date_detection",
        "extrait",
        "date_limite",
        "domaine_officiel_probable",
        "statut"
    ]

    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for c in candidates:
            writer.writerow({
                "titre": c.title,
                "lien_officiel": c.official_link,
                "site_source": c.source_site,
                "url_decouverte": c.discovery_url,
                "date_detection": c.detection_date,
                "extrait": c.snippet,
                "date_limite": c.deadline or "",
                "domaine_officiel_probable": c.domain_type,
                "statut": c.status
            })

    return file_path


def export_to_markdown(candidates: List[ScholarshipCandidate], output_dir: str = "data") -> Path:
    """
    Génère un rapport Markdown clair et lisible des nouvelles bourses.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    file_path = out_path / f"candidates_{today_str}.md"

    lines = [
        f"# 🎓 Nouvelles Bourses Candidates ({today_str})",
        "",
        f"**Nombre de nouveaux candidats détectés :** {len(candidates)}",
        "",
        "---",
        ""
    ]

    if not candidates:
        lines.append("_Aucune nouvelle bourse détectée lors de ce passage._\n")
    else:
        for idx, c in enumerate(candidates, start=1):
            deadline_str = f"⏳ **Date limite :** {c.deadline}" if c.deadline else "⏳ **Date limite :** Non précisée"
            domain_badge = f"🏛️ **Type :** `{c.domain_type}`"

            lines.extend([
                f"### {idx}. [{c.title}]({c.official_link})",
                f"- 🌐 **Source :** {c.source_site} | [Page source]({c.discovery_url})",
                f"- {domain_badge} | {deadline_str} | 📅 Détecté le : {c.detection_date}",
                f"- 📝 **Extrait :** {c.snippet or 'Non disponible'}",
                ""
            ])

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return file_path
