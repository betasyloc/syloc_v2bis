"""Contenu générique des modèles de bail et d'état des lieux (modifiables par le bailleur)."""

DEFAULT_LEASE_TEMPLATE = """BAIL DE LOCATION D'HABITATION NON MEUBLÉE

Entre les soussignés :
- Le bailleur : [Nom du bailleur]
- Le preneur : [Nom du locataire]

Il a été convenu ce qui suit :

Article 1 – Objet
Le présent bail consentit un droit de location sur le bien suivant : [Adresse du bien].

Article 2 – Durée
Le bail est consenti pour une durée de [X] an(s) à compter du [date de début]. Il sera renouvelé par tacite reconduction par périodes successives de [X] an(s), sauf dénonciation.

Article 3 – Loyer et charges
Le loyer mensuel est fixé à [montant] €. Les charges récupérables s'élèvent à [montant] € par mois. Le total à payer chaque mois est de [total] €. Le loyer et les charges sont payables d'avance, à terme échu, le [jour] de chaque mois.

Article 4 – Dépôt de garantie
Un dépôt de garantie de [montant] € est versé par le preneur. Il sera restitué en fin de bail, déduction faite des sommes restant dues.

Article 5 – Destination des lieux
Les lieux sont donnés à usage d'habitation exclusive. Toute sous-location ou cession du bail est interdite sans accord écrit du bailleur.

Article 6 – Entretien et réparations
Le preneur est tenu d'entretenir les lieux et d'effectuer les menues réparations. Les grosses réparations restent à la charge du bailleur.

Article 7 – Assurances
Le preneur souscrit une assurance multirisque habitation couvrant sa responsabilité civile. Le bailleur est en droit d'en demander justification.

Article 8 – État des lieux
Un état des lieux contradictoire sera établi à l'entrée et à la sortie des lieux.

Fait en deux exemplaires originaux.
À [Ville], le [Date].

Signature du bailleur                    Signature du preneur
"""

DEFAULT_INSPECTION_TEMPLATE = """ÉTAT DES LIEUX – POINTS À VÉRIFIER

Général
- Nettoyage général
- Clés remises
- Compteurs (eau, électricité, gaz) : relevés

Pièce par pièce
- Murs et plafonds : fissures, traces, peinture
- Sol : état, taches, usure
- Menuiseries (portes, fenêtres) : fermeture, vitres, joints
- Radiateurs / chauffage : fonctionnement
- Prises et interrupteurs : fonctionnement
- Éclairage : fonctionnement

Cuisine
- Électroménager (si fourni) : état
- Robinetterie, évacuation
- Placards : état intérieur

Salle de bain / WC
- Carrelage, jointures
- Robinetterie, chasse d'eau
- Ventilation

Dégagements
- Couloir, palier : état des murs et sols

Observations complémentaires :
[À remplir lors de la visite]
"""


def get_default_lease_content() -> str:
    return DEFAULT_LEASE_TEMPLATE.strip()


def get_default_inspection_content() -> str:
    return DEFAULT_INSPECTION_TEMPLATE.strip()
