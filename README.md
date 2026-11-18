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
   - Les agrégateurs surveillés et les liens impossibles à vérifier sont écartés ; le lien de l'article n'est jamais utilisé comme solution de repli.
   - Pour les sources qui masquent ce lien (ex. **youthmedia.net**, réservé aux abonnés connectés), une **recherche web** via Gemini + Google Search retrouve la page de l'organisme. Le résultat est vérifié en HTTP, puis mémorisé dans `data/official_links_cache.json` : aucune recherche n'est jamais repayée deux fois. Le nombre de recherches par exécution est limité par `LINK_SEARCH_MAX_PER_RUN` (25 par défaut), et chaque lancement reprend là où le précédent s'est arrêté.
   - **La date limite de la page officielle fait foi** : si elle diffère de celle de la source de veille, elle est corrigée, et une bourse déjà fermée selon l'organisme est écartée.

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
  │   ├── official_link_finder.py # Recherche web du lien officiel (sources sans lien sortant)
  │   ├── supabase_sync.py     # Importation automatique en brouillon dans DrenoLearn
  │   ├── dedup.py             # Normalisation d'URL et fuzzy matching
  │   ├── storage.py           # Gestion SQLite (déjà vu)
  │   ├── platform_sync.py     # Synchronisation avec le sitemap DrenoLearn
  │   ├── exporters.py         # Générateurs CSV et Markdown
  │   ├── notifier.py          # Envoi d'alertes e-mail SMTP
  │   ├── models.py            # Modèle de données Pydantic
  │   └── main.py              # Interface en ligne de commande (CLI)
  ├── tests/                   # Tests unitaires et d'intégration
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
python -m src.main test-source youthmedia
```

### 🚀 2. Lancer la découverte complète
Scrape toutes les sources actives, écarte les bourses déjà sur DrenoLearn et les doublons, enregistre les nouveaux candidats et génère les exports du jour :
```powershell
python -m src.main run
```

Chaque appel Gemini est limité à 20 secondes et n'est pas relancé automatiquement par le SDK. Si la synchronisation Supabase échoue, le candidat n'est pas marqué comme traité et pourra être retenté au prochain lancement.

Pour cibler une seule source lors de l'exécution réelle :
```powershell
python -m src.main run --source greatyop
```

### 📣 Préparer les publications Canva et Buffer

Après publication d'une bourse sur le site (`is_active = true` dans Supabase), lance la préparation locale :

```powershell
python -m src.main prepare-social
```

À chaque exécution quotidienne à 18 h, le traitement prend les nouvelles opportunités déjà publiées sur DrenoLearn (`is_active = true`), rédige leur texte avec OpenAI et crée leur idée Buffer **non programmée**. Les contenus s'accumulent dans un lot : sous 21, les idées Buffer existent déjà mais aucun CSV Canva n'est généré. Dès que 21 contenus du lot ont leur idée Buffer créée, le système produit un ou deux CSV (bourses et stages) contenant ensemble 21 lignes. Les opportunités suivantes formeront le lot suivant. Les brouillons non publiés sur DrenoLearn ne sont jamais inclus.

Dans Canva, importe le bon CSV via **Création en bloc** et associe `DATE_LIMITE`, `NIVEAU_ETUDE` et `DESCRIPTION_BLOC` aux champs du modèle. `ID_BOURSE` sert à retrouver l'idée correspondante dans Buffer; `SUGGESTION_IMAGE` est un mot-clé pour choisir le fond. Ces deux colonnes ne sont pas à associer au visuel. Après validation du visuel, ajoute-le à l'idée Buffer correspondante, puis programme toi-même le post pour Facebook et LinkedIn. Il n'y a pas de programmation automatique ni de commande de confirmation à exécuter.

Variables locales requises dans `.env` : `OPENAI_KEY` (ou `OPENAI_API_KEY`) et `BUFFER_API_KEY`. Si Buffer donne accès à plusieurs organisations, renseigne aussi `BUFFER_ORGANIZATION_ID`. Le modèle OpenAI (`OPENAI_MODEL`) et la taille du lot (`SOCIAL_CSV_BATCH_SIZE`, 21 par défaut) sont configurables. Le texte reprend le modèle éditorial DrenoLearn; les dates, critères et avantages absents des informations publiées sont omis et ne doivent pas être inventés.

La tâche Windows quotidienne à 18:00 lance `python.exe -m src.main prepare-social` depuis le répertoire du projet. Le PC doit être allumé et connecté à Internet. Chaque nouveau contenu publié traité consomme un appel OpenAI et crée une idée Buffer. Le premier CSV du lot sort lorsque la 21e idée est créée; si un appel échoue, le CSV attend que les 21 idées soient présentes. Les résultats incertains Buffer doivent être vérifiés avant une relance.

Si le traitement signale un résultat Buffer incertain, vérifie d'abord dans Buffer que l'idée n'a pas été créée. Seulement après cette vérification, tu peux relancer pour cet identifiant :

```powershell
python -m src.main retry-social-buffer IDENTIFIANT_SUPABASE --confirmed-no-idea
```

Pour la conception cible de l'automatisation hébergée sur Vercel, les tables Supabase, le lot de 21 et les écrans de gestion du dashboard Next.js, voir [le document d'automatisation des publications sociales](./docs/automatisation-publications-sociales.md). Cette intégration hébergée reste à réaliser dans le dépôt Next.js de DrenoLearn; le présent dépôt est le scraper Python.

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
3. Ajoute `SUPABASE_SERVICE_ROLE_KEY` pour que les nouvelles bourses soient créées en brouillon dans DrenoLearn.
4. Ajoute `GEMINI_API_KEY` pour activer l'enrichissement des bourses avec Gemini.
5. Pour recevoir les alertes par e-mail, ajoute les secrets suivants :
   - `EMAIL_NOTIFICATIONS_ENABLED` : `true`
   - `SMTP_HOST` : `smtp.gmail.com`
   - `SMTP_PORT` : `587`
   - `SMTP_USER` : ton adresse Gmail
   - `SMTP_PASSWORD` : ton mot de passe d'application Google
   - `ALERT_EMAIL_RECIPIENT` : l'adresse de réception
6. Après fusion du workflow dans la branche par défaut, le robot tournera **automatiquement tous les jours à 07h00 UTC**.
7. Tu pourras aussi le déclencher à la demande depuis l'onglet **Actions > Découverte Quotidienne des Bourses > Run workflow**.
