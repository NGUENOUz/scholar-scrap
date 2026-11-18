# Automatisation des publications sociales DrenoLearn

## 1. Objectif

Automatiser la préparation des publications Facebook et LinkedIn dès qu'une opportunité est réellement publiée sur DrenoLearn, sans laisser un ordinateur local allumé. Le flux automatise la rédaction et la création d'idées Buffer, mais conserve les contrôles humains pour les visuels Canva et la programmation finale.


## 2. Règles fonctionnelles convenues

- Seules les opportunités publiées sur le site (`is_active = true`) sont admissibles. Les brouillons que l'administrateur n'a pas encore vérifiés ne sont jamais traités.
- Le passage d'une opportunité de brouillon à publiée déclenche la préparation sans attendre 18 h.
- Pour chaque opportunité publiée, le système demande à OpenAI le texte social et les champs Canva, puis crée une idée Buffer non programmée.
- Les idées et les données Canva s'accumulent dans un lot.
- À la création réussie de la 21e idée du lot, le système génère un ou deux CSV Canva contenant ensemble les 21 opportunités (un fichier pour les bourses et un autre pour les stages, si les deux types sont présents).
- Un lot incomplet n'est pas exporté. Il attend les prochaines publications.
- Canva Bulk Create, la vérification des visuels, leur ajout aux idées Buffer et la programmation Facebook/LinkedIn restent manuels.
- Une fois le lot exporté, les prochaines opportunités alimentent le lot suivant.
- Le premier lot commence à la date d'activation de cette fonctionnalité. Les opportunités déjà publiées avant cette date ne sont pas importées automatiquement; un administrateur peut les ajouter explicitement s'il le souhaite. Ainsi, les brouillons déjà présents peuvent d'abord être vérifiés et publiés sans déclencher le traitement social pendant cette phase de validation.

La taille du lot doit être configurable côté serveur (21 par défaut), mais modifiable uniquement par une personne autorisée. Une modification de taille ne doit pas diviser ni mélanger silencieusement un lot déjà commencé.

## 3. Flux cible

```text
Admin publie une bourse dans le dashboard Next.js
                 |
                 v
Supabase enregistre is_active: false -> true
                 |
                 v
Un lot courant reçoit un emplacement (1 à 21)
                 |
                 v
Un travail idempotent est créé dans l'outbox Supabase
                 |
                 +--> OpenAI rédige le texte social et les champs Canva
                 |
                 +--> Buffer crée une idée non programmée
                 |
                 v
État, texte, champs Canva et lien de l'idée enregistrés dans Supabase
                 |
       21 idées Buffer confirmées dans le lot ?
                 |
          non ---+--- oui
          |           |
          v           v
    Progression     Créer le(s) CSV Canva et
    visible dans    enregistrer le(s) fichier(s)
    le dashboard    dans Supabase Storage
                        |
                        +--> Envoyer un email de lot prêt
                          |
                          v
              Admin télécharge et importe dans Canva
                          |
                          v
              Admin valide le visuel, l'ajoute
              à l'idée Buffer, puis programme
              Facebook et LinkedIn manuellement
```

Il faut distinguer la **publication sur DrenoLearn** (déclencheur automatique) de la **publication sur les réseaux** (qui reste manuelle dans Buffer après validation du visuel).

## 4. Architecture recommandée sur Vercel et Supabase

### Déclencheur à la publication

Le backend doit détecter une transition de `is_active` de `false` à `true`, et non chaque mise à jour de la ligne. Deux options sont possibles :

1. **Outbox dans Supabase et worker Vercel (recommandé)** : dans la transaction qui publie une fiche, un trigger SQL vérifie la transition `false -> true`, réserve atomiquement un emplacement dans le lot courant et ajoute un travail dans l'outbox. Un worker déclenché par webhook traite le travail rapidement.
2. **Webhook Supabase vers un Route Handler Next.js** : un événement `UPDATE` appelle une route serveur Vercel. La route valide l'authenticité de l'appel, vérifie la transition et insère le travail idempotent. L'insertion doit rester transactionnelle ou être protégée par des contraintes uniques.

Pour une première version fiable, l'outbox Supabase est recommandée : le changement de statut, la réservation du lot et la création du travail sont persistés par la base, même si Vercel ou Buffer est momentanément indisponible. Un webhook peut réveiller le worker immédiatement. Un Vercel Cron périodique peut servir de mécanisme de rattrapage si un webhook est manqué. Le Cron seul n'est pas instantané : son intervalle détermine le délai.

Le handler doit répondre rapidement et ne pas dépendre d'un processus de fond non garanti après la fin de la requête Vercel. Si le traitement dépasse la durée autorisée par le plan Vercel, il faut traiter les travaux individuellement dans des invocations courtes ou utiliser une file/worker géré. Aucun processus serveur permanent n'est nécessaire.

### Responsabilités des composants

- **Dashboard Next.js** : publier les bourses, afficher l'état de préparation sociale, proposer les contrôles autorisés et télécharger les CSV.
- **Supabase** : source de vérité des opportunités publiées, file de travaux, état des publications et stockage des CSV.
- **Route Handler / worker serveur** : vérifier les événements, traiter un travail, appliquer les protections contre les doublons et appeler les fournisseurs externes.
- **OpenAI API** : produire le texte et les valeurs de colonnes à partir des informations de la fiche publiée.
- **Buffer API** : créer une idée au niveau de l'organisation sans date ni programmation.
- **Canva** : rester un flux manuel via Bulk Create/CSV; aucune API Canva payante n'est requise par cette architecture.

## 5. Données à persister

Les données de préparation sociale ne doivent pas être conservées uniquement dans SQLite sur le PC ou le disque temporaire d'une fonction Vercel. Il faut une persistance centrale dans Supabase.

Une conception possible comprend :

### `social_post_batches` — le lot persistant

- `id` : identifiant du lot.
- `sequence_number` : numéro lisible du lot.
- `status` : `collecting`, `needs_attention`, `exporting`, `ready` ou `completed`.
- `target_count` : 21 par défaut.
- `assigned_count` : nombre d'opportunités affectées au lot, maximum 21.
- `buffer_created_count` : nombre d'idées Buffer confirmées, de 0 à 21.
- `created_at`, `ready_at`, `completed_at`.
- `csv_storage_paths` : références vers les CSV du lot dans Supabase Storage.
- `notification_sent_at` ou état équivalent pour éviter les emails en double.

Le lot **se trouve dans la base Supabase**, dans `social_post_batches`; ce n'est ni un fichier local, ni une tâche en mémoire sur Vercel. Les lignes détaillées se trouvent dans `social_posts` et référencent leur lot par `batch_id`. Les CSV générés sont des objets persistants dans un bucket Supabase Storage privé.

### `social_posts`

- `id`, `scholarship_id` (clé étrangère vers `bourses`) et `batch_id`.
- `status` : état de rédaction, Buffer et export.
- `template_type` : `bourse` ou `stage`.
- Les champs Canva : date limite, niveau, description courte et suggestion d'image.
- `social_text`, `public_url`.
- `buffer_idea_id`, `buffer_status`, `buffer_error`.
- `created_at`, `updated_at`.

Une contrainte unique sur `scholarship_id` empêche de créer plusieurs idées pour une même opportunité. Les travaux de la file doivent eux aussi avoir une clé d'idempotence unique liée à l'identifiant de la bourse et à l'événement de publication. La réservation d'un emplacement (1 à 21) doit être atomique : deux publications concurrentes ne doivent ni prendre le même emplacement, ni dépasser 21 dans le même lot.

Lorsqu'un lot a déjà reçu ses 21 emplacements mais qu'un traitement est en échec, les nouvelles publications restent en attente dans l'outbox; elles ne doivent pas faire déborder ni remplacer les entrées du lot. Le lot courant attend la résolution de l'échec et l'export, puis le prochain lot commence. Le compteur « Buffer créé » n'avance qu'après confirmation de Buffer, jamais à la simple réception du webhook ou à la création du travail.

### `social_publication_jobs` ou outbox

- `id`, `scholarship_id`, `status` (`pending`, `processing`, `retry`, `done`, `failed`).
- `attempt_count`, `last_error`, `available_at`, `locked_at`.
- `idempotency_key`, horodatages.

Ne pas mettre de tokens, clés API ou en-têtes d'authentification dans ces tables.

## 6. États et comportement en cas d'erreur

Les états doivent permettre à l'administrateur de comprendre ce qui s'est passé et d'intervenir sans créer de doublon :

1. `pending` : publication détectée, travail en attente.
2. `generating` : rédaction OpenAI en cours.
3. `buffer_pending` : texte prêt, création Buffer à effectuer ou à relancer après une erreur certaine.
4. `buffer_created` : idée Buffer confirmée; compte dans le seuil du lot.
5. `needs_review` : résultat Buffer incertain (par exemple timeout après envoi); vérifier Buffer avant de retenter.
6. `csv_ready` : CSV du lot stocké et téléchargeable.
7. `ready` : les 21 idées Buffer sont confirmées, le(s) CSV est/sont stocké(s) et l'email a été envoyé ou est en cours de reprise.
8. `completed` : l'administrateur a confirmé le traitement manuel du lot, si un suivi de fin de cycle est souhaité.

Une erreur OpenAI ne doit pas produire un texte de secours présenté comme valide. Un échec certain de Buffer peut être retenté avec une limite et une temporisation. En cas de timeout ou de réponse ambiguë après une mutation Buffer, il faut éviter le retry automatique : rechercher l'idée dans Buffer ou utiliser une clé d'idempotence si l'API la prend en charge, puis demander confirmation avant une nouvelle création.

Le CSV ne doit être généré que lorsque les 21 entrées affectées au lot ont leur idée Buffer confirmée et leurs champs Canva valides. Un échec d'export doit pouvoir être relancé sans rappeler OpenAI ni recréer les idées Buffer. L'export doit utiliser un nom unique et ne jamais écraser silencieusement un CSV précédent. L'email n'est envoyé qu'après vérification de l'écriture complète et de la disponibilité de tous les CSV.

## 7. CSV Canva

Le(s) fichier(s) doit/doivent contenir ensemble exactement les 21 entrées du lot terminé :

| Colonne | Utilisation |
|---|---|
| `ID_BOURSE` | Retrouver la fiche et l'idée correspondante dans le dashboard/Buffer |
| `DATE_LIMITE` | Champ du modèle Canva |
| `NIVEAU_ETUDE` | Champ du modèle Canva |
| `DESCRIPTION_BLOC` | Résumé court du programme pour le bloc blanc du modèle |
| `SUGGESTION_IMAGE` | Mot-clé indicatif pour choisir le fond; pas nécessairement lié à un champ Canva |

Répartir les lignes dans `canva_bourses_<lot>.csv` et `canva_stages_<lot>.csv` lorsque nécessaire. Vérifier que la somme des lignes est égale à 21 avant de marquer le lot comme prêt. Enregistrer les fichiers dans un bucket Supabase Storage privé. Le téléchargement depuis le dashboard doit vérifier le rôle admin puis fournir un lien signé de durée limitée. Les fichiers ne doivent pas être écrits sur le disque temporaire Vercel en supposant qu'ils y resteront disponibles.

Exemple : si un lot contient 13 bourses et 8 stages, générer deux CSV de 13 et 8 lignes respectivement. Si les 21 entrées sont des bourses, générer un seul CSV de 21 lignes. Le nom des fichiers doit inclure le numéro de lot et être unique.

## 8. Écrans de gestion dans le dashboard admin

Ajouter une section **Réseaux sociaux** dans le dashboard, réservée aux administrateurs.

### Vue d'ensemble et barre de progression

- Barre de progression du lot courant : `x / 21 idées Buffer confirmées`. Le numérateur compte les idées Buffer dont la création est confirmée, et non le nombre de brouillons DrenoLearn publiés ni les travaux simplement démarrés.
- Sous la barre, afficher séparément les nombres `affectées au lot`, `OpenAI terminés`, `Buffer confirmés`, `en attente` et `à vérifier`. En cas d'échec, expliquer pourquoi la progression est bloquée et proposer l'action de reprise adaptée.
- Mettre à jour la barre sans rechargement manuel, par Supabase Realtime si cette fonctionnalité est déjà utilisée de façon fiable dans le projet, sinon par rafraîchissement périodique côté client.
- Nombre d'opportunités publiées encore à traiter.
- Nombre d'erreurs et d'états nécessitant vérification.
- Date et résultat de la dernière exécution.
- Liste des derniers lots, état et lien de téléchargement des CSV.

### Détail du lot

- Les 21 fiches avec titre, type (bourse/stage), lien public et identifiant.
- État OpenAI, état Buffer, date de création et message d'erreur non sensible.
- Lien vers l'idée Buffer si l'API fournit une URL stable.
- Colonnes Canva, accessibles pour contrôle avant l'export.
- Action « Réessayer » seulement lorsqu'un retry est sûr.
- Action « Marquer comme vérifié » pour un résultat Buffer incertain, avec confirmation explicite.
- Téléchargement du ou des CSV une fois le seuil atteint.
- État de notification email du lot.

### Email « lot Canva prêt »

Lorsque les 21 idées Buffer du lot sont confirmées et que tous les CSV sont présents dans Supabase Storage :

- envoyer un email aux destinataires administrateurs configurés côté serveur;
- inclure le numéro du lot, le compteur `21/21`, le nombre de bourses et de stages, et un lien sécurisé vers le dashboard;
- fournir dans le dashboard les liens de téléchargement des CSV (le courriel peut inclure des liens signés de durée limitée, mais ne doit pas joindre de fichier sensible ni utiliser une URL publique permanente);
- expliquer que les 21 idées Buffer sont des brouillons non programmés et que la création/validation Canva et la programmation des réseaux restent manuelles;
- enregistrer l'état et l'horodatage d'envoi pour éviter les doublons;
- en cas d'échec d'envoi, garder le lot `ready` avec une notification en attente et permettre un renvoi sans régénérer les CSV ni recréer les idées.

Utiliser le fournisseur email transactionnel déjà présent dans le projet s'il existe. Sinon, proposer un fournisseur et une configuration serveur après vérification de l'environnement; les identifiants doivent rester dans les secrets Vercel.

### Paramètres

- Taille de lot par défaut : 21.
- Modèle OpenAI autorisé, si le choix doit être configurable.
- Activation/désactivation de la préparation automatique.
- Destinataires des emails « lot prêt », modifiables uniquement par un administrateur ou par configuration serveur.
- Aucun secret API affiché; les secrets sont configurés dans Vercel Environment Variables ou un gestionnaire de secrets.

### Suivi du travail manuel

Un bouton facultatif « Lot traité dans Canva et Buffer » peut servir au suivi interne. Il ne doit pas laisser entendre que le système vérifie réellement la programmation Buffer si cette synchronisation API n'est pas implémentée. Le prochain lot peut commencer à recevoir des publications une fois le lot précédent exporté et son CSV disponible; il ne dépend pas de la saisie manuelle des dates de programmation.

## 9. Sécurité

- Mettre `OPENAI_API_KEY` (ou le nom de variable choisi côté serveur) et `BUFFER_API_KEY` uniquement dans les variables d'environnement serveur Vercel. Elles ne doivent jamais utiliser le préfixe `NEXT_PUBLIC_`, être livrées au navigateur ou apparaître dans les logs.
- Utiliser la clé Supabase service role uniquement côté serveur, avec les droits minimum requis; ne jamais l'exposer au client.
- Authentifier le webhook Supabase vers Vercel, vérifier les événements et refuser les requêtes non autorisées.
- Protéger les routes admin avec la session et le rôle administrateur, puis vérifier les autorisations côté serveur même si l'interface masque les boutons.
- Utiliser RLS Supabase pour empêcher les utilisateurs ordinaires de lire/écrire les files, textes internes et CSV.
- Ne pas placer les textes complets ou informations privées des candidats dans les logs.
- Protéger contre les injections d'instructions : les champs de la fiche sont des données, pas des instructions pour OpenAI. Ne pas inventer dates, financements, critères ou avantages.
- Définir des plafonds d'usage et alertes de coûts OpenAI dans le tableau de bord OpenAI. Le quota API réel dépend du projet, du modèle et du niveau d'usage; le tarif API est distinct d'un abonnement ChatGPT.

## 10. Estimation de volume et coûts OpenAI

Le modèle OpenAI doit être choisi et configuré côté serveur. Un lot complet requiert environ **21 appels de rédaction** (un par opportunité), plus les éventuels retries. Le coût dépend des tokens d'entrée et de sortie par fiche; il n'existe pas de quota journalier universel identique pour tous les comptes.

Avant le déploiement :

1. vérifier les tarifs du modèle choisi sur [la page officielle des tarifs API OpenAI](https://developers.openai.com/api/docs/pricing);
2. vérifier les limites propres au projet dans la page Limits de l'OpenAI Platform;
3. mesurer quelques générations représentatives et calculer `coût moyen par post × 21`;
4. configurer une limite mensuelle et des alertes de dépense;
5. enregistrer les nombres de tokens et le coût si les réponses de l'API fournissent ces métriques, sans enregistrer la clé.

L'API OpenAI fonctionne à l'usage; un abonnement ChatGPT ne constitue pas un quota d'appels API.

## 11. Déploiement progressif

### Phase A — Vérification du projet

- Confirmer le schéma réel de `public.bourses`, les règles RLS, les rôles du dashboard et la manière dont le bouton Publier modifie `is_active`.
- Confirmer l'hébergement Vercel, le plan et ses limites de fonctions, ainsi que la structure de l'application Next.js.
- Vérifier l'organisation Buffer, les permissions et la mutation `createIdea`.
- Vérifier le fournisseur email existant et ses capacités.
- Choisir le bucket Storage et la politique d'accès aux CSV.

### Phase B — Persistance et détection

- Ajouter les tables migrations nécessaires et leurs index/contraintes uniques.
- Ajouter l'outbox Supabase et l'affectation atomique à un lot lors de la transition `false -> true`.
- Tester le déclencheur avec une fausse publication, sans appeler OpenAI ou Buffer.
- Ajouter une route serveur protégée et un mécanisme de reprise des travaux manqués.

### Phase C — OpenAI et Buffer

- Implémenter la rédaction dans un module serveur TypeScript en respectant le modèle éditorial DrenoLearn et les règles de véracité.
- Ajouter les tests unitaires avec des clients OpenAI/Buffer simulés.
- Créer des idées Buffer uniquement en environnement de test ou avec une publication explicitement choisie.
- Tester les retries, timeouts, doublons et résultats Buffer incertains.

### Phase D — Lots, barre de progression, CSV et email

- Vérifier qu'aucun export n'est créé pour 20 contenus et qu'un export contenant 21 lignes est créé au 21e.
- Tester un lot mixte de bourses et stages et vérifier que les deux CSV contiennent 21 lignes au total.
- Stocker et télécharger le CSV avec les permissions admin.
- Vérifier qu'un échec d'export n'entraîne aucun nouvel appel OpenAI/Buffer.
- Vérifier que la barre admin affiche 0/21, progresse uniquement après confirmation Buffer, et affiche clairement erreurs et reprises.
- Vérifier qu'un email est envoyé une seule fois après la création et la validation de tous les CSV.
- Simuler un échec d'email et vérifier que le renvoi ne régénère ni CSV ni idées Buffer.

### Phase E — Dashboard et mise en production

- Ajouter la vue d'ensemble, la barre de progression, le détail des lots, les téléchargements, les notifications email et les reprises sécurisées.
- Lancer d'abord en mode observation ou sur une bourse test contrôlée.
- Comparer les résultats générés à la fiche publiée; faire valider les textes et le visuel manuellement.
- Activer le traitement automatique seulement après validation des journaux, alertes et protections contre les doublons.

## Consignes d'implémentation

Examiner d'abord l'application Next.js existante : structure du dashboard admin, modèle d'authentification/autorisation, schéma Supabase et migrations, méthode de publication des bourses, fonctions Vercel, gestion des secrets, fournisseur email, conventions UI et tests. Réutiliser les mécanismes déjà présents lorsqu'ils conviennent; ne pas supposer que les tables ou composants décrits ici existent déjà et ne pas créer de doublons de fonctionnalités.

Implémenter le flux complet dans l'application existante, y compris les migrations, le déclenchement fiable, le traitement idempotent, la génération et le stockage des CSV, la notification email, la barre de progression et les vues admin. Ne pas exposer les clés OpenAI, Buffer ou Supabase au navigateur. Garder la création des visuels Canva, leur ajout aux idées Buffer et la programmation des réseaux sous validation manuelle.

La mise en service doit permettre de choisir une date d'activation : ne pas traiter rétroactivement les opportunités déjà publiées avant cette date. Après activation, seules les transitions nouvelles de brouillon vers publié alimentent les lots, afin de laisser l'administrateur vérifier ses brouillons existants avant leur publication.

Considérer l'implémentation terminée lorsque les tests démontrent au minimum les scénarios suivants :

1. Une publication après activation réserve exactement un emplacement et ne crée qu'un seul travail, même si le webhook est livré plusieurs fois.
2. À `20/21`, aucun CSV ni email « lot prêt » n'est produit; le dashboard montre `20/21`.
3. À `21/21` avec les 21 idées Buffer confirmées, le(s) CSV est/sont créé(s), stocké(s), téléchargeable(s) par un administrateur et l'email est envoyé une seule fois.
4. Les CSV d'un lot mixte ont un total de 21 lignes et séparent correctement bourses et stages.
5. Une erreur OpenAI, Buffer, Storage ou email est visible dans le dashboard et peut être reprise sans doublon ni génération inutile.
6. Les publications reçues alors que le lot courant est plein attendent le lot suivant sans être perdues ni ajoutées au-delà de 21.
7. Les brouillons et les opportunités déjà publiées avant l'activation ne déclenchent pas de traitement implicite.
8. Les utilisateurs non administrateurs ne peuvent ni consulter les données internes ni télécharger les CSV.
