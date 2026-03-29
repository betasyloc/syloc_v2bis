# Déployer SyLoc sur Render (URL stable)

Ce guide permet d’obtenir une **URL stable** (ex. `https://syloc-xxxx.onrender.com`) pour votre bêta SyLoc.

> **Vous préférez une méthode plus simple, sans Blueprint ?** → Utilisez **[DEPLOIEMENT_SIMPLE.md](DEPLOIEMENT_SIMPLE.md)** : vous créez la base et le site à la main, étape par étape dans l’interface Render.

## Prérequis

- Un compte [Render](https://render.com) (gratuit)
- Le projet SyLoc poussé sur **GitHub** ou **GitLab** (Render se connecte au dépôt)

---

## Étape 1 : Pousser le code sur Git

Si ce n’est pas déjà fait :

```bash
cd C:\Users\papem\immo_saas
git init
git add .
git commit -m "Préparation déploiement Render"
git remote add origin https://github.com/VOTRE_USER/VOTRE_REPO.git
git push -u origin main
```

Remplacez `VOTRE_USER` et `VOTRE_REPO` par votre dépôt. Si le dépôt existe déjà, un simple `git push` suffit après avoir ajouté les nouveaux fichiers.

---

## Étape 2 : Créer le Blueprint sur Render

1. Allez sur [dashboard.render.com](https://dashboard.render.com) et connectez-vous.
2. Cliquez sur **« New + »** → **« Blueprint »**.
3. Connectez votre compte **GitHub** ou **GitLab** si ce n’est pas déjà fait, puis choisissez le **dépôt** qui contient SyLoc.
4. **Important** : si le dossier du projet (avec `manage.py`, `requirements.txt`) n’est pas à la **racine** du dépôt mais dans un sous-dossier (ex. `immo_saas`), indiquez ce dossier dans **« Root Directory »** : `immo_saas`.
5. Render détecte le fichier `render.yaml`. Cliquez sur **« Apply »** pour créer le service web et la base PostgreSQL.
6. Attendez la fin du premier déploiement (quelques minutes). En cas d’erreur, consultez les **logs** du service.

---

## Étape 3 : Récupérer votre URL stable

Une fois le déploiement réussi :

1. Dans le **Dashboard Render**, ouvrez le service **syloc** (type Web Service).
2. En haut, vous voyez l’URL du type : **`https://syloc-xxxx.onrender.com`**.
3. C’est **votre URL de connexion** pour les testeurs.

Vous pouvez la copier dans **BETA_TESTEURS.md** à la section « URL de connexion ».

---

## Étape 4 : Créer un compte admin (optionnel)

La base est vide après le premier déploiement. Pour créer un superutilisateur :

1. Dans le service **syloc**, onglet **« Shell »** (ou **« Manual Deploy »** → **Shell** selon l’interface).
2. Ouvrez un shell et lancez :
   ```bash
   python manage.py createsuperuser
   ```
3. Entrez un identifiant (email), un mot de passe, etc.

Les testeurs peuvent aussi s’inscrire via **« Créer un compte »** sur la page de connexion si vous avez laissé l’inscription ouverte.

---

## Variables d’environnement utiles

Dans **Dashboard** → **syloc** → **Environment**, vous pouvez ajouter ou modifier :

| Variable | Valeur | Rôle |
|----------|--------|------|
| `DEBUG` | `false` | Déjà dans `render.yaml` ; à garder en prod. |
| `ALLOWED_HOSTS` | `.onrender.com` | Déjà défini ; si vous ajoutez un nom de domaine plus tard, ajoutez-le ici (séparé par une virgule). |
| `DEFAULT_FROM_EMAIL` | ex. `noreply@votredomaine.com` | Pour les emails (rappels, réinitialisation mot de passe). |
| `EMAIL_*` | (SMTP) | En prod, configurez un vrai serveur SMTP pour que les emails partent (sinon ils restent en console). |

---

## Nom de domaine personnalisé (optionnel)

Pour une URL du type **https://beta.syloc.fr** :

1. Dans le service **syloc**, onglet **« Settings »** → **« Custom Domain »**.
2. Ajoutez `beta.syloc.fr` (ou le domaine de votre choix).
3. Render vous indique quoi configurer chez votre registrar (enregistrement CNAME).
4. Dans **Environment**, ajoutez ce domaine à **ALLOWED_HOSTS** : `beta.syloc.fr,.onrender.com`.

---

## En résumé

- **URL stable** = celle affichée sur la fiche du service **syloc** (ex. `https://syloc-xxxx.onrender.com`).
- Mettez cette URL dans **BETA_TESTEURS.md** et communiquez-la à vos testeurs.
- Les données sont stockées dans la base PostgreSQL Render ; le disque de l’app est éphémère (fichiers uploadés à prévoir ailleurs si besoin plus tard).
- **Offre gratuite** : le service web s’arrête après quelques minutes d’inactivité. Le premier chargement après une pause peut prendre 30–60 secondes (réveil du serveur).
