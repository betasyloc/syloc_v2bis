# SyLoc

**Le logiciel le plus simple pour gérer ses locations sans prise de tête.**

SaaS destiné aux propriétaires particuliers (1 à 8 biens), investisseurs LMNP et salariés investis dans le locatif.

## Démarrage

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # optionnel
python manage.py runserver
```

Ouvrir http://127.0.0.1:8000/

## Fonctionnalités V1

- **Session** : Connexion / Déconnexion
- **Biens** : Ajout, modification, suppression, archivage
- **Locataires** : Ajout, modification, suppression, archivage
- **Baux** : Création, détail, modification, suppression, archivage ; rédaction et mise à jour du texte du bail
- **Cashflow** : Calcul automatique (loyers encaissés − crédit − charges)
- **Relances impayés** : Relance automatique par email (`send_rent_reminders`) : paliers **J+7, J+15, J+30** (règles Premium)
- **Quittances** : Génération PDF
- **Dashboard** : Vue rentabilité, loyers en retard
- **États des lieux** : Entrée / sortie par bail, avec notes
- **Archives** : Archivage biens, locataires, baux ; filtre « Voir les archives »

## Rappels et relances (cron)

À planifier quotidiennement (ex. 8h) :

```bash
python manage.py send_rent_reminders
# Option : --dry-run
```

## Export des données (sauvegarde / portabilité)

Pour exporter toutes les données d’un compte (biens, locataires, baux, loyers, états des lieux) en JSON :

```bash
python manage.py export_user_data email@exemple.com
# ou par ID : python manage.py export_user_data --user 1
# optionnel : -o mon_export.json
```

Fichier généré : `export_syloc_<email>_<date>.json` (sauf si `-o` est précisé).

## Confidentialité et données

Les données sont isolées par utilisateur (chaque compte ne voit que ses biens, locataires et baux). Le pied de page renvoie vers les pages **Mentions légales**, **Confidentialité** et **Cookies** (`/mentions-legales/`, `/confidentialite/`, `/cookies/`). Renseignez les variables `SYLOC_LEGAL_*` dans l’environnement pour afficher l’éditeur du service sur les mentions légales.

**Droit à l’effacement (compte bailleur)** : depuis **Mon profil** (`/compte/profil/`), lien vers la suppression définitive du compte (`/compte/suppression/`) après confirmation par mot de passe et saisie de `SUPPRIMER`. Les comptes super-utilisateur Django ne sont pas supprimables par ce flux.

## Configuration (variables d'environnement)

Voir `.env.example` pour la liste des variables. En production, définir au minimum `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS` et, pour Stripe, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_BASE` (basic mensuel) et `STRIPE_PRICE_ID_PREMIUM` (ou IDs renseignés sur les plans en admin).

### Stripe (abonnements Premium)

1. Créer un produit et un prix récurrent dans le [Dashboard Stripe](https://dashboard.stripe.com/products).
2. Définir `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID_BASE` (basic mensuel), `STRIPE_PRICE_ID_PREMIUM` (et optionnellement `STRIPE_PRICE_ID_PREMIUM_ANNUAL` pour un prix annuel) ou renseigner `stripe_price_id` / `stripe_price_id_annual` sur les plans en admin.
3. Webhook : URL `https://votre-domaine.com/webhooks/stripe/`, événements recommandés `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_succeeded` (optionnel) ; définir `STRIPE_WEBHOOK_SECRET`.
4. En local : `stripe listen --forward-to localhost:8000/webhooks/stripe/` et utiliser le secret affiché.

#### Tester le webhook en local (Stripe CLI)

Après avoir lancé Django (`python manage.py runserver`) :

```bash
# 1) Écouter et forwarder vers ton endpoint
stripe listen --forward-to localhost:8000/webhooks/stripe/
```

- Récupère la valeur `whsec_...` affichée et mets-la dans `.env` via `STRIPE_WEBHOOK_SECRET`, puis redémarre le serveur Django.

```bash
# 2) Déclencher un événement de test (exemples)
stripe trigger customer.subscription.created
stripe trigger customer.subscription.updated
stripe trigger customer.subscription.deleted
```

Les abonnés Premium peuvent utiliser « Gérer mon abonnement » (portail client Stripe) pour modifier le moyen de paiement ou annuler.

### OpenAI (recommandations IA – Premium)

Pour activer les **recommandations IA** sur la page « Rentabilité IA » (analyse personnalisée par bien) :

1. Créer un compte sur [platform.openai.com](https://platform.openai.com) et générer une clé API.
2. Définir la variable d’environnement **`OPENAI_API_KEY`** avec cette clé (ex. dans un fichier `.env` ou dans les paramètres d’hébergement).
3. Optionnel : **`OPENAI_MODEL`** (défaut : `gpt-4o-mini`). Ex. `gpt-4o` pour des analyses plus détaillées.
4. Redémarrer l’application.

Sans clé, la page affiche une analyse par règles (indicateurs + texte fixe). Ne jamais commiter la clé dans le dépôt.

## Fonctionnalités Premium

- **Multi-utilisateurs** (à venir)
- **Export comptabilité CSV** (abonnés Premium)
- Synchronisation bancaire
- Signature électronique
- Export calendrier (ICS)

## Évolutivité et mises à jour

- **Documentation** : [docs/EVOLUTIVITE.md](docs/EVOLUTIVITE.md) pour ajouter des fonctionnalités, gérer les migrations et la scalabilité.
- **Changelog** : [CHANGELOG.md](CHANGELOG.md) pour l’historique des modifications.

## Cahier des charges

Voir [CAHIER_DES_CHARGES.md](CAHIER_DES_CHARGES.md).
