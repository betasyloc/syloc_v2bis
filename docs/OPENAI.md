# Configurer OpenAI pour les recommandations IA (Premium)

La page **Rentabilité IA** (menu Premium) affiche pour chaque bien une analyse de rentabilité. Pour des **recommandations générées par IA** (OpenAI), configurez une clé API.

## 1. Obtenir une clé API OpenAI

1. Allez sur [platform.openai.com](https://platform.openai.com).
2. Créez un compte ou connectez-vous.
3. Dans **API keys**, créez une nouvelle clé (Create new secret key).
4. Copiez la clé (elle commence par `sk-...`). Vous ne pourrez plus la revoir après fermeture de la fenêtre.

## 2. Définir la clé dans l’environnement

### En local (fichier .env)

Créez ou éditez un fichier `.env` à la racine du projet (ne pas le commiter) :

```env
OPENAI_API_KEY=sk-votre-cle-ici
```

Si vous chargez ce fichier avec `python-dotenv` ou un outil similaire, assurez-vous qu’il est lu au démarrage (ex. dans `manage.py` ou avant d’importer Django). Sinon, exportez la variable dans le terminal :

- **Windows (PowerShell)** : `$env:OPENAI_API_KEY="sk-votre-cle-ici"`
- **Linux / Mac** : `export OPENAI_API_KEY=sk-votre-cle-ici`

### En production (Render, Heroku, etc.)

Dans les paramètres du projet (Variables d’environnement / Environment), ajoutez :

- **Nom** : `OPENAI_API_KEY`
- **Valeur** : votre clé (ex. `sk-...`)

Puis redéployez l’application.

## 3. Modèle (optionnel)

Par défaut, SyLoc utilise le modèle **`gpt-4o-mini`** (bon compromis coût / qualité). Pour changer :

```env
OPENAI_MODEL=gpt-4o
```

Modèles courants : `gpt-4o-mini`, `gpt-4o`, `gpt-4-turbo`.

## 4. Vérifier que c’est actif

1. Connectez-vous en tant qu’utilisateur **Premium**.
2. Allez dans **Rentabilité IA** (menu ou page Premium).
3. Si la clé est correctement configurée, un message indique que l’analyse est générée par l’IA. Les textes par bien sont alors personnalisés (et non plus le texte par règles).

## Sécurité

- **Ne jamais** commiter `OPENAI_API_KEY` dans le dépôt (Git).
- Gardez la clé dans `.env` (ignoré par Git) ou dans les variables d’environnement de l’hébergeur.
- En cas de fuite, révoquez la clé sur platform.openai.com et générez-en une nouvelle.
