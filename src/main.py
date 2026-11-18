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
from src.sources import (
    SourceManager,
    detect_domain_type,
    extract_deadline,
    is_deadline_expired,
    parse_date_safely,
)
from src.storage import Storage
from src.platform_sync import PlatformSync
from src.exporters import export_to_csv, export_to_markdown
from src.notifier import EmailNotifier
from src.link_resolver import resolve_official_link
from src.official_link_finder import reset_run_budget
from src.supabase_sync import SupabaseBourseImporter, extract_country, format_sql_date
from src.ai_enricher import AIEnricher
from src.enrichment_queue import EnrichmentQueue
from src.social_publisher import (
    prepare_social_posts,
    retry_uncertain_buffer_idea
)

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
    supabase_importer = SupabaseBourseImporter()
    platform_sync = PlatformSync(supabase_importer=supabase_importer)
    notifier = EmailNotifier()
    ai_enricher = AIEnricher()
    enrichment_queue = EnrichmentQueue()
    reset_run_budget()

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
    total_unverified_links = 0
    total_queued = 0

    # ---------------------------------------------------------
    # Traitement prioritaire de la file d'attente d'enrichissement
    # ---------------------------------------------------------
    if not dry_run and enrichment_queue.count() > 0:
        print(f"\n🔄 Traitement prioritaire de la file d'attente IA ({enrichment_queue.count()} bourse(s) en attente)...")
        queued_items = enrichment_queue.get_all()
        for item in queued_items:
            cand_dict = item.get("candidate", {})
            try:
                cand = ScholarshipCandidate(**cand_dict)
            except Exception as e:
                logger.error("Candidat invalide dans la file d'attente : %s", e)
                continue

            if platform_sync.is_present_on_platform(cand.title, cand.official_link):
                logger.info("Bourse de la file déjà sur la plateforme : %s", cand.title)
                enrichment_queue.remove(cand.title, cand.official_link)
                continue

            if ai_enricher.is_quota_exhausted:
                print("   ⏳ Quota Gemini indisponible. La file d'attente sera reprise au prochain lancement.")
                break

            date_limite = format_sql_date(cand.deadline)
            enriched_info = ai_enricher.enrich(
                title=cand.title,
                official_link=cand.official_link,
                country=item.get("country", "International"),
                deadline=date_limite,
                scraped_context=item.get("scraped_context", "")
            )

            if ai_enricher.is_quota_exhausted or not enriched_info:
                print(f"   ⏳ Quota atteint lors du dépilement. Reprise au prochain run : {cand.title}")
                enrichment_queue.increment_retry(cand.official_link)
                break

            # Si l'IA a trouvé une date limite absente auparavant
            ai_date = enriched_info.get("date_limite_detectee")
            if not cand.deadline and ai_date and parse_date_safely(ai_date):
                cand.deadline = ai_date

            if is_deadline_expired(cand.deadline):
                logger.info("Bourse de la file écartée car expirée (%s) : %s", cand.deadline, cand.title)
                enrichment_queue.remove(cand.title, cand.official_link)
                continue

            draft_res = supabase_importer.insert_as_draft(cand, cand.official_link, enriched_info)
            if draft_res:
                storage.save_candidate(cand)
                enrichment_queue.remove(cand.title, cand.official_link)
                new_candidates.append(cand)
                print(f"   ✨ [FILE D'ATTENTE] Brouillon créé avec succès : {cand.title} (ID: {draft_res.get('id')})")
            else:
                enrichment_queue.increment_retry(cand.official_link)

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

                # 0 bis. Déjà connue par son titre : inutile de dépenser une recherche de lien.
                if storage.is_title_similar_seen(cand.title):
                    cand.status = "déjà vu"
                    total_duplicates += 1
                    logger.debug("Titre déjà connu, lien non recherché : %s", cand.title)
                    continue

                # Résoudre le lien avant les dédoublonnages pour ne conserver que la source officielle.
                real_official_link = resolve_official_link(
                    cand.official_link,
                    cand.source_site,
                    title=cand.title,
                    hint_text=cand.source_details or cand.snippet
                )
                if not real_official_link:
                    # Si aucun lien officiel externe n'a pu être résolu automatiquement :
                    # Pour les sources à contenu vérifié sans lien sortant (ex: youthmedia),
                    # on ne jette pas l'opportunité ! On retient l'URL source pour validation manuelle.
                    if cand.source_site in ("youthmedia",):
                        logger.info(
                            "Lien externe non trouvé pour '%s' ; conservation de la page source pour vérification manuelle.",
                            cand.title
                        )
                        real_official_link = cand.discovery_url
                        cand.official_link = real_official_link
                        cand.domain_type = "À renseigner"
                    else:
                        total_unverified_links += 1
                        logger.warning(
                            "Aucun lien officiel fiable trouvé ; candidat ignoré : %s (%s)",
                            cand.title, cand.official_link
                        )
                        continue
                else:
                    cand.official_link = real_official_link
                    cand.domain_type = detect_domain_type(real_official_link)

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

                official_context = cand.source_details or cand.snippet

                if not dry_run:
                    official_page = await scraper.scrape(real_official_link)
                    official_text = (
                        official_page.markdown[:20000]
                        if official_page.success and official_page.markdown
                        else ""
                    )

                    # La date limite de la page officielle fait foi sur celle de la source de veille.
                    official_deadline = extract_deadline(official_text) if official_text else None
                    if official_deadline and parse_date_safely(official_deadline):
                        if cand.deadline and official_deadline != cand.deadline:
                            logger.info(
                                "Date limite corrigée depuis la page officielle : '%s' (%s -> %s)",
                                cand.title, cand.deadline, official_deadline
                            )
                        cand.deadline = official_deadline
                        if is_deadline_expired(cand.deadline):
                            cand.status = "expiré"
                            total_expired += 1
                            logger.info(
                                "Bourse écartée : la page officielle indique une date dépassée (%s) pour '%s'",
                                cand.deadline, cand.title
                            )
                            continue

                    if official_text:
                        official_context = official_text
                        if cand.source_details:
                            official_context += (
                                "\n\nDétails fournis par la source de veille (à recouper) :\n"
                                + cand.source_details[:6000]
                            )

                print(f"   ✨ NOUVELLE VALIDE : {cand.title}")
                print(f"      🔗 Vrai lien officiel : {cand.official_link}")
                print(f"      🏛️ {cand.domain_type} | ⏳ {cand.deadline or 'En cours / Non précisée'}")

                if not dry_run:
                    country = extract_country(
                        " ".join(
                            part for part in (cand.title, cand.snippet, cand.source_details) if part
                        )
                    )
                    date_limite = format_sql_date(cand.deadline)

                    # Si le quota Gemini est déjà épuisé, on place immédiatement en file d'attente
                    if ai_enricher.is_quota_exhausted:
                        enrichment_queue.enqueue(
                            candidate=cand,
                            scraped_context=official_context,
                            country=country,
                            deadline=cand.deadline,
                            reason="quota_exhausted"
                        )
                        total_queued += 1
                        print(f"      ⏳ [FILE D'ATTENTE] Quota Gemini épuisé. Enregistré pour le prochain démarrage.")
                        continue

                    enriched_info = ai_enricher.enrich(
                        title=cand.title,
                        official_link=real_official_link,
                        country=country,
                        deadline=date_limite,
                        scraped_context=official_context
                    )

                    # Si l'enrichissement IA a échoué (quota 429 ou indisponibilité), mise en file d'attente
                    if ai_enricher.is_quota_exhausted or not enriched_info:
                        reason = ai_enricher.last_failure_reason or "ai_failure"
                        enrichment_queue.enqueue(
                            candidate=cand,
                            scraped_context=official_context,
                            country=country,
                            deadline=cand.deadline,
                            reason=reason
                        )
                        total_queued += 1
                        print(f"      ⏳ [FILE D'ATTENTE] Échec enrichissement ({reason}). Enregistré pour le prochain démarrage.")
                        continue

                    # Récupération de la date détectée par l'IA si aucune date n'avait été trouvée par regex
                    ai_date = enriched_info.get("date_limite_detectee")
                    if not cand.deadline and ai_date and parse_date_safely(ai_date):
                        cand.deadline = ai_date
                        logger.info("Date limite détectée par l'IA pour '%s' : %s", cand.title, cand.deadline)
                        if is_deadline_expired(cand.deadline):
                            cand.status = "expiré"
                            total_expired += 1
                            logger.info("Bourse écartée : date détectée par l'IA expirée (%s) pour '%s'", cand.deadline, cand.title)
                            continue

                    # Insertion automatique en brouillon dans DrenoLearn
                    draft_res = supabase_importer.insert_as_draft(cand, real_official_link, enriched_info)
                    if draft_res:
                        print(f"      📥 [DRENOLEARN] Brouillon complet créé avec succès (ID: {draft_res.get('id')})")
                    elif supabase_importer.client:
                        logger.warning(
                            "Candidat non inséré dans Supabase (déjà présent ou publié) : %s",
                            cand.title
                        )
                        continue

                    if not storage.save_candidate(cand):
                        logger.warning("Candidat non enregistré localement : %s", cand.title)
                        continue

                new_candidates.append(cand)

    print("\n" + "=" * 60)
    print(f"📊 BILAN DE L'EXÉCUTION :")
    print(f"   - Articles analysés : {total_found}")
    print(f"   - Bourses expirées / clôturées écartées : {total_expired}")
    print(f"   - Déjà présentes sur ta plateforme : {total_on_platform}")
    print(f"   - Doublons locaux écartés : {total_duplicates}")
    print(f"   - Liens officiels non vérifiés écartés : {total_unverified_links}")
    print(f"   - Mises en file d'attente (quota/échec IA) : {total_queued} (Total en attente: {enrichment_queue.count()})")
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
    reset_run_budget()

    print(f"\n🚀 Synchronisation et enrichissement IA de {len(candidates)} bourses vers Supabase DrenoLearn...")
    updated_count = 0

    async with PoliteScraper(delay_seconds=float(os.getenv("SCRAPE_DELAY_SECONDS", "3.0"))) as scraper:
        for idx, cand in enumerate(candidates, 1):
            print(f"\n[{idx}/{len(candidates)}] {cand.title}...")
            real_link = resolve_official_link(
                cand.official_link,
                cand.source_site,
                title=cand.title,
                hint_text=cand.source_details or cand.snippet
            )
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

    # Préparation des publications sociales pour Canva et Buffer
    social_parser = subparsers.add_parser(
        "prepare-social",
        help="Crée les brouillons Buffer des contenus publiés et exporte chaque lot Canva complet"
    )
    social_parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Nombre de contenus requis pour générer un CSV Canva (21 par défaut)"
    )

    retry_social_parser = subparsers.add_parser(
        "retry-social-buffer",
        help="Relance une création d'idée Buffer dont le résultat était incertain"
    )
    retry_social_parser.add_argument("scholarship_id", help="Identifiant Supabase de la bourse")
    retry_social_parser.add_argument(
        "--confirmed-no-idea",
        action="store_true",
        help="Confirme que tu as vérifié dans Buffer que l'idée n'existe pas déjà"
    )

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
    elif args.command == "prepare-social":
        if args.batch_size is not None and args.batch_size < 1:
            parser.error("--batch-size doit être supérieur à zéro.")
        result = prepare_social_posts(
            importer=SupabaseBourseImporter(),
            storage=Storage(),
            batch_size=args.batch_size
        )
        print(
            f"\nIdées Buffer créées : {result['created_ideas']} "
            f"(progression du lot : {result['batch_progress']}/{result['batch_size']}; "
            f"reste avant CSV : {result['remaining_to_batch']})."
        )
        if result["csv_files"]:
            print("Fichiers Canva à importer dans Création en bloc :")
            for path in result["csv_files"]:
                print(f"   - {path}")
        else:
            print("Aucun nouveau CSV Canva à générer.")
        for item in result["buffer_review"]:
            print(
                f"⚠️ Résultat Buffer à vérifier dans l'application pour "
                f"{item['title']} (bourse {item['scholarship_id']})."
            )
    elif args.command == "retry-social-buffer":
        if not args.confirmed_no_idea:
            parser.error(
                "Vérifie d'abord dans Buffer que l'idée n'existe pas, puis ajoute --confirmed-no-idea."
            )
        retry_uncertain_buffer_idea(args.scholarship_id, Storage())


if __name__ == "__main__":
    main()
