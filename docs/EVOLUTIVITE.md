# Évolutivité et mises à jour SyLoc

Ce document décrit comment faire évoluer SyLoc de manière maîtrisée (scalabilité, nouvelles fonctionnalités, déploiement).

## Structure du projet

- **`immo_saas/`** : configuration Django (settings, urls racine, WSGI).
- **`rental/`** : application métier (modèles, vues, formulaires, templates).
- **`templates/`** : templates globaux (base, auth).
- **`static/`** : fichiers statiques (CSS, JS).
- **`docs/`** : documentation (ce fichier, déploiement, etc.).

Les réglages sensibles passent par des **variables d'environnement** (voir `.env.example`). Ne pas hardcoder de secrets.

## Ajouter une nouvelle fonctionnalité

1. **Modèles** : définir dans `rental/models.py` (ou un nouveau module `rental/models_*.py` si le domaine grossit), puis `python manage.py makemigrations -n description_courte`.
2. **Vues** : ajouter les vues dans `rental/views.py` (ou un sous-module `rental/views_*.py` si besoin), en réutilisant les helpers existants (`properties_visible_to`, `_require_premium`, etc.).
3. **URLs** : enregistrer dans `rental/urls.py` (namespace `rental`) ou dans `immo_saas/urls.py` pour les pages racine.
4. **Templates** : sous `templates/rental/` en étendant `base.html`.
5. **Tests** : ajouter des tests dans `rental/tests/` si pertinent.
6. **Migrations** : toujours créer et appliquer les migrations avant déploiement ; ne pas modifier à la main des migrations déjà appliquées.

## Migrations en production

- Faire des sauvegardes (base, fichiers) avant toute migration.
- Tester les migrations en staging (même version de Django et de la base).
- Les migrations de données (RunPython) doivent être idempotentes ou réversibles quand c’est possible (RunPython.reverse_code).

## Scalabilité technique

- **Base de données** : en production, utiliser PostgreSQL (déjà supporté via `DATABASE_URL`). Indexer les champs utilisés dans les filtres et les jointures.
- **Cache** : configurer Redis (`REDIS_URL`) pour le cache et, si besoin, le rate limiting multi-worker.
- **Statiques** : WhiteNoise est utilisé ; pour de très gros volumes, envisager un CDN.
- **Tâches asynchrones** : pour des envois d’emails ou des exports lourds, prévoir à terme une file (Celery, Redis, etc.) et une commande management dédiée.

## Journal des modifications (admin)

Les actions effectuées dans l’admin Django sont enregistrées dans **AdminActionLog** (date/heure, utilisateur, type d’action, objet, commentaire). Utiliser le champ « Commentaire » pour documenter explicitement la raison d’une modification.

## IA analyse rentabilité (Premium)

La page « Rentabilité IA » affiche pour chaque bien les indicateurs (loyer, charges, crédit, cashflow, rendement brut) et un texte d’analyse. Si `OPENAI_API_KEY` est défini dans les variables d’environnement, l’analyse est générée par OpenAI (modèle `OPENAI_MODEL`, défaut `gpt-4o-mini`). Sinon, une analyse par règles (texte fixe à partir des indicateurs) est affichée.

## Notifications et annonces

Les messages destinés aux utilisateurs (nouveautés, maintenance) sont gérés via le modèle **Announcement**. Les publications sont affichées dans l’onglet « Notifications » (sous Déconnexion dans le menu).

## Versions et déploiement

- Documenter les changements importants dans `CHANGELOG.md`.
- En production : déployer avec une fenêtre de maintenance si les migrations sont longues ; vérifier les variables d’environnement et les secrets (Stripe, email, etc.).
