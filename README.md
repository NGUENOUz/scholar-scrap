# 🎓 Scholarship Discovery - Service de Détection de Bourses

Service autonome, 100% gratuit et respectueux des sites sources, conçu pour découvrir automatiquement de nouvelles opportunités de bourses d'études et alimenter la plateforme [DrenoLearn](https://www.drenolearn.com).

---

## 🌟 Fonctionnalités clés

1. **Scraping respectueux & économe en RAM :**
   - Utilise [Crawl4AI](https://github.com/unclecode/crawl4ai) avec Chromium headless.
   - Respecte strictement les règles du fichier `robots.txt` de chaque source.
   - Ne traite qu'**une seule page à la fois** avec un délai de courtoisie (pause) entre les requêtes pour protéger ta mémoire vive (RAM) et éviter tout blocage.

2. **Résolution des VRAIS liens officiels :**
   - Ne renvoie **aucun lien vers tes concurrents**.
   - Visite la page de l'article agrégateur et extrait le lien officiel de l'université, du gouvernement ou de l'organisme hôte (boutons *Postuler*, *Site officiel*, domaines `.edu`, `.gouv`, etc.).

3. **Création automatique en mode Brouillon (`is_active = false`) sur DrenoLearn :**
   - Connecté directement à ta table Supabase `public.bourses`.
   - Dès qu'une opportunité est détectée, elle est automatiquement créée dans ta base DrenoLearn avec toutes les contraintes respectées (`slug`, `titre`, `description`, `pays`, `type_financement`, `date_limite`, `lien_officiel`).
   - Statut par défaut : `is_active = false` (brouillon). Tu n'as plus qu'à vérifier et cliquer sur "Publier" dans ton panneau admin !

4. **Dédoublonnage intelligent & Synchronisation DrenoLearn :**
   - **Plateforme DrenoLearn :** Lit automatiquement et sans surcoût le sitemap (`https://drenolearn.com/sitemap.xml`) une seule fois par exécution pour écarter tout programme déjà présent.
   - **Normalisation d'URL :** Élimine les balises UTM de tracking, ancres `#`, préfixes `www.` et protocoles.
   - **Similarité floue (Fuzzy Matching) :** Compare les titres via `rapidfuzz`.
   - **Déjà vu :** Une bourse déjà détectée n'est **jamais** renvoyée une seconde fois.

5. **Prise en compte des bourses encore valides :**
   - Ne se limite pas aux bourses publiées aujourd'hui : explore les catalogues pour dénicher tout programme **encore ouvert / valide**.
   - Écarte automatiquement les bourses expirées.

---

## 📁 Arborescence du Projet

```text
scholarship-discovery/
  ├── config/
  │   └── sources.yaml         # Liste des sites surveillés + sélecteurs
  ├── data/
  │   ├── scholarships.db      # Base SQLite locale des bourses déjà vues
  │   ├── candidates_*.csv     # Exports CSV quotidiens
  │   └── candidates_*.md      # Rapports Markdown quotidiens
  ├── src/
  │   ├── scraper.py           # Porte d'entrée unique vers Crawl4AI + robots.txt
  │   ├── sources.py           # Extraction & analyse des dates limites
  │   ├── link_resolver.py     # Résolution du VRAI lien officiel (anti-concurrent)
  │   ├── supabase_sync.py     # Importation automatique en brouillon dans DrenoLearn
  │   ├── dedup.py             # Normalisation d'URL et fuzzy matching
  │   ├── storage.py           # Gestion SQLite (déjà vu)
  │   ├── platform_sync.py     # Synchronisation avec le sitemap DrenoLearn
  │   ├── exporters.py         # Générateurs CSV et Markdown
  │   ├── notifier.py          # Envoi d'alertes e-mail SMTP
  │   ├── models.py            # Modèle de données Pydantic
  │   └── main.py              # Interface en ligne de commande (CLI)
  ├── tests/                   # 14 tests unitaires validés
  ├── .github/workflows/       # Automatisation quotidienne GitHub Actions
  ├── requirements.txt         # Dépendances Python
  └── .env.example             # Modèle de configuration
```

---

## 🚀 Installation Rapide (Guide Débutant)

### 1. Prérequis
- Python 3.10 ou supérieur installé sur ton ordinateur.
- Un terminal (PowerShell sur Windows, Terminal sur Mac/Linux).

### 2. Activer l'environnement virtuel

Dans le dossier `scholarship-discovery` :

```powershell
# Sur Windows :
.\.venv\Scripts\Activate.ps1

# Sur Linux / macOS :
source .venv/bin/activate
```

*(Si l'environnement virtuel n'est pas encore créé, tape `python -m venv .venv` puis installe les dépendances avec `pip install -r requirements.txt`)*.

### 3. Configurer tes alertes (Optionnel)

Copie `.env.example` en `.env` :
```powershell
copy .env.example .env
```
Ouvre `.env` et renseigne tes identifiants e-mail si tu veux recevoir les alertes sur ta boîte :
- Pour Gmail, utilise un **Mot de passe d'application** (sécurité Google > Mots de passe des applications).
- Passe `EMAIL_NOTIFICATIONS_ENABLED=true`.

---

## 💻 Commandes Disponibles

### 🔍 1. Tester une source sans rien enregistrer (Mode Test)
Permet de visualiser ce que le robot extrait sur une source sans modifier la base de données et sans envoyer d'e-mail :
```powershell
python -m src.main test-source greatyop
python -m src.main test-source maboursedetudes
python -m src.main test-source scholarships365
```

### 🚀 2. Lancer la découverte complète
Scrape toutes les sources actives, écarte les bourses déjà sur DrenoLearn et les doublons, enregistre les nouveaux candidats et génère les exports du jour :
```powershell
python -m src.main run
```

Pour cibler une seule source lors de l'exécution réelle :
```powershell
python -m src.main run --source greatyop
```

### 📊 3. Exporter l'historique
Génère un export CSV et Markdown de tous les candidats actuellement enregistrés :
```powershell
python -m src.main export
```

### 🧪 4. Lancer les tests de validation
Vérifie en quelques secondes que tous les composants fonctionnent correctement :
```powershell
pytest -v
```

---

## 🌐 Déploiement Gratuit sur GitHub Actions

Le workflow `.github/workflows/daily_discovery.yml` est déjà configuré :
1. Crée un dépôt GitHub (privé de préférence) et pousse ton code.
2. Va dans l'onglet **Settings > Secrets and variables > Actions** de ton dépôt GitHub.
3. Ajoute les secrets suivants :
   - `EMAIL_NOTIFICATIONS_ENABLED` : `true`
   - `SMTP_HOST` : `smtp.gmail.com`
   - `SMTP_PORT` : `587`
   - `SMTP_USER` : ton adresse Gmail
   - `SMTP_PASSWORD` : ton mot de passe d'application Google
   - `ALERT_EMAIL_RECIPIENT` : l'adresse de réception
4. Le robot tournera désormais **automatiquement tous les matins à 07h00 UTC**.
5. Tu pourras aussi le déclencher à la demande depuis l'onglet **Actions > Découverte Quotidienne des Bourses > Run workflow**.
