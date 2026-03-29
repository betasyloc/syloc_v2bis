# Partager SyLoc avec des testeurs (ngrok)

Avec ngrok, vous lancez SyLoc sur votre PC et vous obtenez un **lien internet** à envoyer aux testeurs. Aucun compte Render ni déploiement.

**Limite :** le lien change à chaque fois que vous relancez ngrok, et votre PC doit rester allumé pendant que les testeurs se connectent.

---

## Étape 1 : Installer ngrok

1. Allez sur **https://ngrok.com** et créez un compte gratuit (ou connectez-vous avec Google/GitHub).
2. Une fois connecté, allez dans **« Your Authtoken »** (ou **Download** → onglet **Setup**). Copiez votre **authtoken** (une longue ligne de caractères).
3. Téléchargez ngrok pour Windows : **https://ngrok.com/download** → cliquez sur **Windows**.
4. Décompressez le fichier ZIP. Vous obtenez un fichier **`ngrok.exe`**. Placez-le où vous voulez (ex. `C:\Users\papem\ngrok\ngrok.exe`).
5. Ouvrez **PowerShell** ou **Invite de commandes**. Tapez (en adaptant le chemin si besoin) :
   ```text
   C:\Users\papem\ngrok\ngrok.exe config add-authtoken VOTRE_TOKEN_ICI
   ```
   Remplacez `VOTRE_TOKEN_ICI` par le token copié à l’étape 2. Validez avec Entrée.

---

## Étape 2 : Lancer SyLoc

1. Ouvrez un terminal dans le dossier du projet SyLoc (ex. `C:\Users\papem\immo_saas`).
2. Activez l’environnement virtuel :
   ```text
   .venv\Scripts\activate
   ```
3. Démarrez le serveur :
   ```text
   python manage.py runserver
   ```
4. Laissez cette fenêtre ouverte. Vous devez voir quelque chose comme : `Starting development server at http://127.0.0.1:8000/`.

---

## Étape 3 : Lancer ngrok

1. Ouvrez **une deuxième** fenêtre PowerShell ou Invite de commandes (ne fermez pas celle où SyLoc tourne).
2. Lancez ngrok (adaptez le chemin si votre `ngrok.exe` est ailleurs) :
   ```text
   C:\Users\papem\ngrok\ngrok.exe http 8000
   ```
3. ngrok affiche des lignes de log. Repérez une ligne du type :
   ```text
   Forwarding   https://xxxx-xx-xx-xx-xx.ngrok-free.app -> http://localhost:8000
   ```
4. **Copiez** l’URL **https://...ngrok-free.app** (c’est le lien à donner aux testeurs).

---

## Étape 4 : Donner le lien aux testeurs

- Envoyez-leur l’URL (ex. par email ou message).
- Vous pouvez aussi la noter dans **BETA_TESTEURS.md** à la place de « *(à compléter…)* » pour l’URL de connexion.  
  **Attention :** cette URL change à chaque nouveau lancement de ngrok. Pensez à la mettre à jour dans le guide ou à la renvoyer aux testeurs si vous redémarrez ngrok.

---

## Arrêter

- Dans la fenêtre où ngrok tourne : **Ctrl+C** pour arrêter ngrok.
- Dans la fenêtre où SyLoc tourne : **Ctrl+C** pour arrêter le serveur.

---

## Résumé

| Étape | Action |
|-------|--------|
| 1 | Installer ngrok, ajouter l’authtoken |
| 2 | Lancer SyLoc : `python manage.py runserver` (fenêtre 1) |
| 3 | Lancer ngrok : `ngrok http 8000` (fenêtre 2), copier l’URL https |
| 4 | Envoyer cette URL aux testeurs |
