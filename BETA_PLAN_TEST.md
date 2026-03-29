# Plan de beta test SyLoc

## 1) Objectifs

- Valider la stabilite de SyLoc en conditions reelles (desktop + mobile).
- Verifier que les parcours critiques sont fluides et comprehensibles.
- Identifier les bugs, regressions et points UX a corriger avant ouverture plus large.

## 2) Profils des beta testeurs

- 6 a 12 testeurs.
- Profils cibles:
  - proprietaire particulier,
  - investisseur LMNP,
  - utilisateur peu technique.
- Repartition conseillee:
  - 50% desktop,
  - 50% smartphone.

## 3) Duree et organisation

- Duree totale: 2 semaines.
- Semaine 1: decouverte et scenarios de base.
- Semaine 2: usage realiste, cas limites, retours UX.

## 4) Environnement de test

- URL de test (preprod/ngrok).
- Comptes de test a preparer:
  - 1 compte proprietaire Premium,
  - 1 compte membre basic,
  - 1 acces locataire via lien portail.
- Jeu de donnees minimum:
  - 2 biens,
  - 2 locataires,
  - 2 baux actifs,
  - plusieurs loyers (payes, a venir, en retard).

## 5) Scenarios obligatoires

### A. Inscription et connexion

- Creer un compte via formule basic.
- Creer un compte via formule Premium.
- Verifier que le choix de formule est bien conserve jusqu'a la creation du compte.
- Se connecter / se deconnecter.

### B. Gestion locative de base

- Ajouter, modifier, supprimer un bien.
- Ajouter, modifier, supprimer un locataire.
- Creer, modifier, supprimer un bail.
- Verifier la generation de quittance PDF.

### C. Loyers

- Marquer un loyer comme paye.
- Verifier le statut en retard / a venir.
- Verifier les filtres et l'affichage de la liste des loyers.

### D. Equipe

- Ajouter un membre basic dans une equipe Premium.
- Verifier que les donnees (biens, locataires, baux) sont partagees dans l'organisation.
- Verifier que les restrictions Premium restent appliquees selon le plan du membre.

### E. Portail locataire

- Generer un lien portail locataire.
- Ouvrir le lien et verifier l'acces:
  - OK: bail et quittances du locataire,
  - KO: autres pages de l'application.
- Tester le bouton "Quitter le mode locataire".

### F. Mobile

- Tester connexion, navigation, formulaires et portail locataire sur smartphone.
- Verifier lisibilite et ergonomie (boutons, champs, scroll, tableaux).

## 6) Mode de remontee des retours

Pour chaque bug/remarque, demander:

- Contexte (page/fonction).
- Etapes de reproduction.
- Resultat obtenu.
- Resultat attendu.
- Capture ecran ou video.
- Impact:
  - P0 Bloquant,
  - P1 Majeur,
  - P2 Mineur,
  - P3 Cosmétique.

Canal recommande:

- Formulaire centralise (Google Form / Notion form).
- Canal rapide de suivi (WhatsApp/Discord/Slack).

## 7) Priorisation et tri

- P0: action immediate.
- P1: correction prioritaire avant sortie.
- P2: correction planifiee sprint suivant.
- P3: lot UX/cosmetique.

## 8) Critères de sortie de beta

- 0 bug P0 ouvert.
- Tous les P1 corriges ou planifies avec date.
- Taux de reussite > 90% sur les scenarios obligatoires.
- Satisfaction testeurs >= 4/5 sur simplicite d'usage.

## 9) Checklist testeur (a cocher)

- [ ] Inscription basic testee
- [ ] Inscription Premium testee
- [ ] Connexion/deconnexion testee
- [ ] CRUD bien teste
- [ ] CRUD locataire teste
- [ ] CRUD bail teste
- [ ] Loyer marque paye teste
- [ ] Quittance PDF telechargee
- [ ] Equipe: ajout membre teste
- [ ] Portail locataire: acces restreint valide
- [ ] Bouton "Quitter le mode locataire" teste
- [ ] Test mobile valide
- [ ] Au moins 1 retour detaille envoye

## 10) Message type a envoyer aux testeurs

Bonjour,

Merci de participer au beta test de SyLoc.
Votre mission: tester les scenarios fournis et remonter chaque bug avec etapes + capture.

Ce que nous voulons valider en priorite:

- inscription/connexion,
- loyers et quittances,
- equipe,
- portail locataire (acces restreint),
- usage mobile.

Merci pour votre aide, vos retours sont essentiels pour finaliser une version fiable.
