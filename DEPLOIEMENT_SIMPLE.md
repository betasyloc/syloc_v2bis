# Déployer SyLoc sur Render – Méthode simple (sans Blueprint)

Ce guide décrit une méthode **étape par étape** dans l’interface Render, sans passer par le Blueprint. Vous créez d’abord la base de données, puis le site, en remplissant des champs un par un.

---

## Avant de commencer

- Vous avez un compte **Render** et un compte **GitHub**.
- Le projet SyLoc est poussé sur GitHub (un dépôt avec le dossier du projet dedans).

---

## Connecter GitHub à Render (à faire en premier si besoin)

Si, à l’étape 2, vous ne voyez **pas** de bouton pour autoriser Render à voir GitHub (par ex. pas de « Configurer compte » ou « Connect account »), connectez GitHub depuis les **paramètres du compte** :

1. Sur [dashboard.render.com](https://dashboard.render.com), cliquez sur **votre profil** (en haut à droite : icône ou initiale).
2. Ouvrez **« Account Settings »** (Paramètres du compte) ou **« Integrations »**.
3. Cherchez la section **« Git »**, **« Git Providers »** ou **« Connected Accounts »**.
4. À côté de **GitHub**, cliquez sur **« Connect »**, **« Link account »** ou **« Configure »**.
5. Une page GitHub s’ouvre : connectez-vous si besoin, puis **autorisez Render** (bouton vert « Authorize » ou « Autoriser »).
6. Revenez sur le dashboard Render. GitHub est maintenant connecté.

Ensuite, reprenez à l’**Étape 1** (créer la base), puis à l’**Étape 2** (Web Service) : la liste de vos dépôts GitHub devrait cette fois s’afficher.

---

## Étape 1 : Créer la base de données

1. Sur [dashboard.render.com](https://dashboard.render.com), cliquez sur **« New + »** (en haut à droite).
2. Choisissez **« PostgreSQL »**.
3. Remplissez :
   - **Name** : `syloc-db` (ou un nom de votre choix).
   - **Region** : laissez par défaut (ex. Frankfurt).
   - **Plan** : **Free**.
4. Cliquez sur **« Create Database »**.
5. Attendez que la base soit prête (statut « Available »). Puis ouvrez-la et, dans l’onglet **« Info »** ou **« Connect »**, copiez la ligne **« Internal Database URL »** (elle ressemble à `postgres://...`). **Gardez-la sous la main** pour l’étape 3.

---

## Étape 2 : Créer le service web (le site)

**En bref :** vous demandez à Render de créer un « site web » qui utilisera le code de votre projet SyLoc stocké sur GitHub. Pour ça, Render doit d’abord **se connecter à GitHub** et **choisir quel dépôt** utiliser.

---

### 2.1 Ouvrir la création d’un Web Service

1. Sur le **dashboard** Render, cliquez sur le bouton **« New + »** (en haut à droite).
2. Dans la liste, cliquez sur **« Web Service »** (c’est le type de service qui affiche un site web avec une URL).

Vous arrivez sur une page qui demande de **connecter un dépôt Git** (repository).

---

### 2.2 Laisser Render accéder à GitHub

Sur cette page, Render a besoin de **voir vos dépôts GitHub**.

- Si vous voyez **« Configurer compte »** ou **« Connect account »** à côté de **GitHub** : cliquez dessus.
- Une page GitHub s’ouvre pour demander **« Autoriser Render »** : acceptez.
- Revenez sur l’onglet Render. Après quelques secondes, la liste des dépôts peut se charger.

**Si vous n’avez pas accès à cette option** (pas de bouton GitHub visible) : connectez GitHub depuis les **paramètres du compte** Render, comme décrit au début de ce guide dans la section **« Connecter GitHub à Render (à faire en premier si besoin) »**. Puis revenez à l’étape 2.

**Si aucun dépôt n’apparaît :** vérifiez que vous avez bien poussé le code SyLoc sur GitHub (au moins un dépôt avec votre projet dedans).

---

### 2.3 Choisir le bon dépôt

- Vous devez **choisir le dépôt** dans lequel se trouve le code de SyLoc.
- Souvent, la liste affiche vos dépôts (ex. `syloc`, `immo_saas`, ou le nom que vous avez donné).
- **Cliquez sur le nom du dépôt** qui contient SyLoc pour le sélectionner (il peut être surligné ou avoir une coche).

Une fois le dépôt sélectionné, Render peut afficher un champ **« Root Directory »** (ou « Répertoire racine »).

---

### 2.4 Le champ « Root Directory » (important)

Render va chercher le fichier `requirements.txt` et la commande Django dans votre dépôt. Il faut lui dire **où** se trouve le projet.

**Cas A – Tout le projet est à la racine du dépôt**

- Sur GitHub, quand vous ouvrez le dépôt, vous voyez tout de suite `manage.py`, `requirements.txt`, le dossier `immo_saas`, etc.
- Alors **laissez « Root Directory » vide** (ne mettez rien).

**Cas B – Le projet est dans un sous-dossier (ex. `immo_saas`)**

- Sur GitHub, quand vous ouvrez le dépôt, vous voyez d’abord un seul dossier (ex. `immo_saas`), et c’est **en cliquant dedans** que vous voyez `manage.py`, `requirements.txt`, etc.
- Alors dans **« Root Directory »**, tapez exactement : **`immo_saas`** (sans slash au début ni à la fin).

En résumé : **Root Directory** = le nom du dossier qui *contient* `manage.py` et `requirements.txt`, si ce dossier n’est pas à la racine. Sinon, laissez vide.

---

### 2.5 Valider et continuer

- Cliquez sur le bouton **« Connect »** ou **« Continue »** (souvent en bas à droite).
- Render charge la configuration du dépôt et vous envoie sur la **page de configuration du service** (étape 3 : Build Command, Start Command, etc.).

**Vous avez terminé l’étape 2** quand vous voyez les champs **Name**, **Build Command**, **Start Command**, etc. C’est là que vous remplirez l’étape 3.

---

## Étape 3 : Configurer le service

Sur la page de configuration du Web Service, remplissez **exactement** ces champs :

| Champ | Valeur à mettre |
|-------|------------------|
| **Name** | `syloc` (ou le nom que vous voulez) |
| **Region** | Même que la base (ex. Frankfurt) |
| **Branch** | `main` (ou la branche où vous poussez le code) |
| **Runtime** | **Python 3** |
| **Build Command** | `pip install -r requirements.txt && python manage.py collectstatic --noinput` |
| **Start Command** | `python manage.py migrate && gunicorn immo_saas.wsgi:application` |

(Pour **Root Directory**, si vous l’avez déjà rempli à l’étape 2, ne changez rien.)

---

## Étape 4 : Ajouter les variables d’environnement

Toujours sur la même page, trouvez la section **« Environment »** ou **« Environment Variables »**, puis ajoutez **une par une** ces variables :

| Clé (Key) | Valeur (Value) |
|-----------|----------------|
| `DJANGO_SECRET_KEY` | Une longue phrase aléatoire (ex. `ma-cle-secrete-tres-longue-123`) |
| `DEBUG` | `false` |
| `ALLOWED_HOSTS` | `.onrender.com` |
| `DATABASE_URL` | Collez ici l’**Internal Database URL** copiée à l’étape 1 |

- Pour `DJANGO_SECRET_KEY`, vous pouvez inventer une chaîne d’une vingtaine de caractères (lettres, chiffres, symboles).
- Ne mettez **pas** de guillemets autour des valeurs.

Cliquez sur **« Add »** ou **« + »** après chaque variable pour en ajouter une autre.

---

## Étape 5 : Lancer le déploiement

1. Vérifiez que les 4 variables sont bien ajoutées.
2. Choisissez le **Plan** : **Free**.
3. Cliquez sur **« Create Web Service »** (ou **« Deploy »**).

Render va alors :

- installer les dépendances ;
- lancer `collectstatic` et `migrate` ;
- démarrer le site avec Gunicorn.

La première fois, cela peut prendre **quelques minutes**. Suivez les **Logs** en bas de la page : s’il y a une erreur, elle apparaîtra là.

---

## Étape 6 : Récupérer votre URL

Quand le déploiement est **réussi** (statut vert / « Live ») :

1. En haut de la fiche du service, vous voyez une URL du type :  
   **`https://syloc-xxxx.onrender.com`**
2. Cliquez dessus (ou copiez-la). C’est **votre URL de connexion** pour SyLoc.
3. Vous pouvez la coller dans **BETA_TESTEURS.md** à la place de « *(à compléter…)* » pour l’URL de connexion.

---

## Créer un compte admin (optionnel)

Pour avoir un superutilisateur (accès admin Django) :

1. Dans le service **syloc**, ouvrez l’onglet **« Shell »** (ou **« Manual Deploy »** puis **Shell**).
2. Dans le shell, tapez :  
   `python manage.py createsuperuser`  
   puis Entrée.
3. Répondez aux questions (email, mot de passe). Les testeurs peuvent aussi s’inscrire via « Créer un compte » sur la page de connexion.

---

## Résumé en 6 étapes

1. **New +** → **PostgreSQL** → créer `syloc-db` → copier l’**Internal Database URL**.
2. **New +** → **Web Service** → connecter le dépôt GitHub (et **Root Directory** si besoin).
3. Remplir **Build Command** et **Start Command** comme dans le tableau.
4. Ajouter les 4 variables d’environnement (dont `DATABASE_URL` avec l’URL copiée).
5. **Create Web Service** et attendre la fin du déploiement.
6. Copier l’URL du service → c’est votre URL stable.

---

## En cas de problème

- **Erreur dans les logs** : vérifiez que **Root Directory** pointe bien vers le dossier qui contient `manage.py` et `requirements.txt` (ex. `immo_saas` si le dépôt contient un dossier `immo_saas`).
- **Page blanche ou 500** : vérifiez que les 4 variables d’environnement sont bien définies et que `DATABASE_URL` est l’**Internal** URL (pas l’External).
- **« DisallowedHost »** : vérifiez que `ALLOWED_HOSTS` vaut bien `.onrender.com` (avec le point au début).

Si vous me dites à quelle étape vous bloquez et le message d’erreur affiché, on peut détailler juste cette partie.
