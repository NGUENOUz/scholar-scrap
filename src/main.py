"""
Module main.py
--------------
Point d'entrée CLI pour le service Scholarship Discovery.

Commandes supportées :
  python -m src.main run
  python -m src.main run --source greatyop
  python -m src.main test-source greatyop
  python -m src.main export
"""

import argparse
import asyncio
import logging
import os
import sys

# Forcer l'encodage UTF-8 pour la console Windows afin d'éviter UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from src.models import ScholarshipCandidate
from src.scraper import PoliteScraper
from src.sources import SourceManager, is_deadline_expired
from src.storage import Storage
from src.platform_sync import PlatformSync
from src.exporters import export_to_csv, export_to_markdown
from src.notifier import EmailNotifier
from src.link_resolver import resolve_official_link
from src.supabase_sync import SupabaseBourseImporter, extract_country, format_sql_date
from src.ai_enricher import AIEnricher

# Charger les variables du fichier .env si présent
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("Main")


async def run_discovery(source_name: str = None, dry_run: bool = False):
    """
    Exécute le pipeline complet de découverte.
    Si dry_run=True, n'enregistre rien et n'envoie aucun e-mail.
    """
    delay = float(os.getenv("SCRAPE_DELAY_SECONDS", "3.0"))
    storage = Storage()
    source_manager = SourceManager()
    platform_sync = PlatformSync()
    notifier = EmailNotifier()
    supabase_importer = SupabaseBourseImporter()
    ai_enricher = AIEnricher()

    # Déterminer les sources à parcourir
    if source_name:
        src_cfg = source_manager.get_source(source_name)
        if not src_cfg:
            print(f"❌ Erreur : Source '{source_name}' introuvable dans config/sources.yaml.")
            return
        sources_to_scrape = [src_cfg]
    else:
        sources_to_scrape = [s for s in source_manager.sources if s.get("enabled", True)]

    print(f"\n🚀 Démarrage du service de découverte ({len(sources_to_scrape)} source(s) active(s))...")
    if dry_run:
        print("⚠️ Mode TEST actif : aucune écriture en base de données, aucun email.")

    # Synchronisation légère avec la plateforme (une seule fois)
    platform_sync.fetch_existing_scholarships()

    new_candidates = []
    total_found = 0
    total_expired = 0
    total_duplicates = 0
    total_on_platform = 0

    async with PoliteScraper(delay_seconds=delay) as scraper:
        for src in sources_to_scrape:
            name = src["name"]
            print(f"\n📂 Source en cours d'analyse : {name} ({src['url']})")

            candidates = await source_manager.scrape_source(scraper, name)
            total_found += len(candidates)
            print(f"   ↳ {len(candidates)} article(s) trouvé(s) au total sur cette source.")

            for cand in candidates:
                # 0. Vérification si la bourse est déjà expirée / clôturée
                if is_deadline_expired(cand.deadline):
                    cand.status = "expiré"
                    total_expired += 1
                    logger.debug("Bourse expirée ignorée : %s (Date limite: %s)", cand.title, cand.deadline)
                    continue

                # 1. Vérification si déjà présent sur la plateforme DrenoLearn
                if platform_sync.is_present_on_platform(cand.title, cand.official_link):
                    cand.status = "sur_plateforme"
                    total_on_platform += 1
                    logger.debug("Déjà sur la plateforme : %s", cand.title)
                    continue

                # 2. Vérification si déjà dans la base SQLite locale (URL)
                if storage.is_url_seen(cand.official_link):
                    cand.status = "déjà vu"
                    total_duplicates += 1
                    logger.debug("Déjà vu en base (URL) : %s", cand.official_link)
                    continue

                # 3. Vérification si titre similaire déjà vu en base
                if storage.is_title_similar_seen(cand.title):
                    cand.status = "déjà vu"
                    total_duplicates += 1
                    logger.debug("Déjà vu en base (titre similaire) : %s", cand.title)
                    continue

                # C'est un nouveau candidat encore VALIDE !
                cand.status = "nouveau"

                # Résolution du VRAI lien officiel (université / gouvernement / fondation)
                real_official_link = cand.official_link
                if not dry_run or True:
                    real_official_link = resolve_official_link(cand.official_link, cand.source_site)
                    cand.official_link = real_official_link

                new_candidates.append(cand)
                print(f"   ✨ NOUVELLE VALIDE : {cand.title}")
                print(f"      🔗 Vrai lien officiel : {cand.official_link}")
                print(f"      🏛️ {cand.domain_type} | ⏳ {cand.deadline or 'En cours / Non précisée'}")

                if not dry_run:
                    storage.save_candidate(cand)
                    official_page = await scraper.scrape(real_official_link)
                    official_context = (
                        official_page.markdown[:20000]
                        if official_page.success and official_page.markdown
                        else cand.snippet
                    )
                    country = extract_country(cand.title + " " + cand.snippet)
                    date_limite = format_sql_date(cand.deadline)
                    enriched_info = ai_enricher.enrich(
                        title=cand.title,
                        official_link=real_official_link,
                        country=country,
                        deadline=date_limite,
                        scraped_context=official_context
                    )
                    # Insertion automatique en brouillon dans DrenoLearn
                    draft_res = supabase_importer.insert_as_draft(cand, real_official_link, enriched_info)
                    if draft_res:
                        print(f"      📥 [DRENOLEARN] Brouillon complet créé avec succès (ID: {draft_res.get('id')})")

    print("\n" + "=" * 60)
    print(f"📊 BILAN DE L'EXÉCUTION :")
    print(f"   - Articles analysés : {total_found}")
    print(f"   - Bourses expirées / clôturées écartées : {total_expired}")
    print(f"   - Déjà présentes sur ta plateforme : {total_on_platform}")
    print(f"   - Doublons locaux écartés : {total_duplicates}")
    print(f"   - Nouvelles bourses valides qualifiées : {len(new_candidates)}")
    print("=" * 60)

    if not dry_run and new_candidates:
        csv_file = export_to_csv(new_candidates)
        md_file = export_to_markdown(new_candidates)
        print(f"\n💾 Exports générés avec succès :")
        print(f"   - CSV      : {csv_file}")
        print(f"   - Markdown : {md_file}")

        # Notification e-mail
        notifier.send_candidates_alert(new_candidates)
    elif dry_run:
        print("\nℹ️ Mode test terminé, aucun fichier enregistré.")
    else:
        print("\nℹ️ Aucun nouveau candidat découvert aujourd'hui.")


async def sync_existing_to_supabase():
    """
    Parcourt les bourses déjà trouvées dans la base SQLite locale,
    résout leur vrai lien officiel, enrichit les détails avec Gemini
    et met à jour les brouillons dans Supabase DrenoLearn.
    """
    storage = Storage()
    importer = SupabaseBourseImporter()
    ai_enricher = AIEnricher()
    candidates = storage.get_candidates(limit=500)

    print(f"\n🚀 Synchronisation et enrichissement IA de {len(candidates)} bourses vers Supabase DrenoLearn...")
    updated_count = 0

    async with PoliteScraper(delay_seconds=float(os.getenv("SCRAPE_DELAY_SECONDS", "3.0"))) as scraper:
        for idx, cand in enumerate(candidates, 1):
            print(f"\n[{idx}/{len(candidates)}] {cand.title}...")
            real_link = resolve_official_link(cand.official_link, cand.source_site)
            official_page = await scraper.scrape(real_link)
            official_context = (
                official_page.markdown[:20000]
                if official_page.success and official_page.markdown
                else cand.snippet
            )
            country = extract_country(cand.title + " " + cand.snippet)
            date_limite = format_sql_date(cand.deadline)

            enriched = ai_enricher.enrich(
                title=cand.title,
                official_link=real_link,
                country=country,
                deadline=date_limite,
                scraped_context=official_context
            )

            res = importer.insert_as_draft(cand, real_link, enriched)
            if res:
                updated_count += 1
                print(f"   ✅ Brouillon enrichi et mis à jour dans Supabase (ID: {res.get('id')})")
            else:
                print("   ℹ️ Erreur ou non mis à jour.")

            await asyncio.sleep(4.0)

    print(f"\n🎉 Synchronisation terminée : {updated_count} bourse(s) enrichie(s) sur DrenoLearn !")


async def enrich_incomplete_drafts(limit: int = 20):
    """Complète les brouillons incomplets sans modifier les bourses publiées."""
    importer = SupabaseBourseImporter()
    ai_enricher = AIEnricher()
    drafts = importer.get_incomplete_drafts(limit=limit)
    print(f"\nBrouillons incomplets sélectionnés : {len(drafts)} (limite : {limit})")
    if not drafts:
        return

    updated_count = 0
    async with PoliteScraper(delay_seconds=float(os.getenv("SCRAPE_DELAY_SECONDS", "3.0"))) as scraper:
        for index, draft in enumerate(drafts, 1):
            title = draft.get("titre") or "Bourse sans titre"
            official_link = draft.get("lien_officiel")
            print(f"\n[{index}/{len(drafts)}] {title}")
            if not official_link:
                print("   ⏭️ Aucun lien officiel, fiche ignorée.")
                continue

            page = await scraper.scrape(official_link)
            context = page.markdown if page.success else ""
            if len(context.strip()) < 100:
                print("   ⏭️ Contenu officiel insuffisant, aucun appel Gemini effectué.")
                continue

            enriched = ai_enricher.enrich(
                title=title,
                official_link=official_link,
                country=draft.get("pays") or "International",
                deadline=draft.get("date_limite") or "Non précisée",
                scraped_context=context[:20000],
                model_names=["gemini-flash-latest"]
            )
            result = importer.update_incomplete_draft(draft["id"], enriched)
            if result:
                updated_count += 1
                print("   ✅ Champs manquants complétés dans le brouillon.")
            else:
                print("   ℹ️ Aucun détail vérifiable à enregistrer.")

            await asyncio.sleep(4.0)

    print(f"\nTerminé : {updated_count}/{len(drafts)} brouillon(s) mis à jour.")


def main():
    parser = argparse.ArgumentParser(
        description="Scholarship Discovery - Service de détection de nouvelles bourses d'études"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Commande run
    run_parser = subparsers.add_parser("run", help="Lance la découverte et enregistre les nouveaux candidats")
    run_parser.add_argument("--source", type=str, default=None, help="Filtrer sur une seule source")

    # Commande test-source
    test_parser = subparsers.add_parser("test-source", help="Teste l'extraction d'une source sans rien enregistrer")
    test_parser.add_argument("source", type=str, help="Nom de la source à tester (ex: greatyop)")

    # Commande export
    subparsers.add_parser("export", help="Génère un export CSV et Markdown de tous les candidats en base")

    # Commande sync-to-supabase
    subparsers.add_parser("sync-to-supabase", help="Envoie toutes les bourses locales dans Supabase DrenoLearn en brouillon")

    # Commande enrich-incomplete
    enrich_parser = subparsers.add_parser("enrich-incomplete", help="Complète les brouillons incomplets sans dépasser la limite d'appels")
    enrich_parser.add_argument("--limit", type=int, default=20, help="Nombre maximal de bourses à traiter (20 par défaut)")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run_discovery(source_name=args.source, dry_run=False))
    elif args.command == "test-source":
        asyncio.run(run_discovery(source_name=args.source, dry_run=True))
    elif args.command == "export":
        storage = Storage()
        all_candidates = storage.get_candidates(limit=500)
        csv_f = export_to_csv(all_candidates)
        md_f = export_to_markdown(all_candidates)
        print(f"Exports générés : {csv_f} et {md_f}")
    elif args.command == "sync-to-supabase":
        asyncio.run(sync_existing_to_supabase())
    elif args.command == "enrich-incomplete":
        asyncio.run(enrich_incomplete_drafts(limit=max(0, min(args.limit, 20))))


if __name__ == "__main__":
    main()
