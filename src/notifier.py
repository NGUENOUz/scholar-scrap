"""
Module notifier.py
------------------
Envoi d'alertes par e-mail (SMTP standard gratuit) et/ou Telegram.
Désactivable simplement via les variables d'environnement (.env).
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import List

from src.models import ScholarshipCandidate

logger = logging.getLogger("Notifier")


class EmailNotifier:
    """Gestionnaire d'envoi d'e-mails d'alerte."""

    def __init__(self):
        self.enabled = os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "false").lower() in ("true", "1", "yes")
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.recipient = os.getenv("ALERT_EMAIL_RECIPIENT", "")

    def send_candidates_alert(self, candidates: List[ScholarshipCandidate]) -> bool:
        """
        Envoie un résumé des nouvelles bourses candidates par e-mail.
        """
        if not self.enabled:
            logger.info("Notifications e-mail désactivées (EMAIL_NOTIFICATIONS_ENABLED=false).")
            return False

        if not candidates:
            logger.info("Aucun nouveau candidat à notifier.")
            return False

        if not (self.smtp_user and self.smtp_password and self.recipient):
            logger.warning("Paramètres SMTP incomplets dans .env. Alerte non envoyée.")
            return False

        today_str = datetime.now().strftime("%Y-%m-%d")
        subject = f"🎓 [DrenoLearn] {len(candidates)} nouvelle(s) bourse(s) découverte(s) - {today_str}"

        # Construction du corps HTML
        html_items = []
        for idx, c in enumerate(candidates, start=1):
            deadline_str = f"⏳ <b>Date limite :</b> {c.deadline}" if c.deadline else "⏳ <b>Date limite :</b> Non précisée"
            snippet_text = c.snippet if c.snippet else "Pas d'extrait disponible."
            html_items.append(f"""
            <div style="margin-bottom: 20px; padding: 12px; border-left: 4px solid #2563eb; background: #f8fafc;">
                <h3 style="margin: 0 0 8px 0;">
                    <a href="{c.official_link}" style="color: #1d4ed8; text-decoration: none;">
                        {idx}. {c.title}
                    </a>
                </h3>
                <p style="margin: 4px 0; color: #475569; font-size: 14px;">
                    🏛️ <b>Domaine :</b> {c.domain_type} | {deadline_str} | 🌐 <b>Source :</b> {c.source_site}
                </p>
                <p style="margin: 6px 0 0 0; color: #334155; font-size: 13px;">
                    <i>{snippet_text}</i>
                </p>
                <p style="margin: 4px 0 0 0; font-size: 12px;">
                    <a href="{c.discovery_url}" style="color: #64748b;">Lien page de découverte</a>
                </p>
            </div>
            """)

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #1e293b; max-width: 650px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;">
                🎓 Service de découverte DrenoLearn
            </h2>
            <p>Voici les <b>{len(candidates)}</b> nouvelles bourses candidates détectées aujourd'hui ({today_str}) :</p>
            {''.join(html_items)}
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-top: 30px;" />
            <p style="font-size: 12px; color: #94a3b8;">
                Notification automatique générée par scholarship-discovery.
            </p>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.smtp_user
        msg["To"] = self.recipient
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            logger.info("Connexion au serveur SMTP %s:%d...", self.smtp_host, self.smtp_port)
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_user, [self.recipient], msg.as_string())
            logger.info("Alerte e-mail envoyée avec succès à %s.", self.recipient)
            return True
        except Exception as e:
            logger.error("Erreur lors de l'envoi de l'e-mail : %s", e)
            return False
