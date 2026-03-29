"""Textes types pour les modèles de lettre (mise en demeure, résiliation, avenant, préavis)."""
from __future__ import annotations

from django.contrib.auth import get_user_model

from .models import LetterTemplate, get_or_create_default_organisation

User = get_user_model()

# Variables (français) : {{locataire}}, {{bien}}, {{adresse}}, {{loyer}}, {{echeance}}, {{nom_bailleur}}

_DEFAULTS: list[tuple[str, str, str]] = [
    (
        LetterTemplate.MISE_EN_DEMEURE,
        "Mise en demeure – loyer impayé",
        """Objet : Mise en demeure de payer les loyers et charges impayés

{{bien}}
{{adresse}}

Madame, Monsieur {{locataire}},

Malgré nos précédents rappels, il reste à ce jour des sommes impayées afférentes au bail concernant le logement désigné ci-dessus.

Conformément aux dispositions du bail et au Code civil, je vous mets en demeure de régler sous huit (8) jours à compter de la réception de la présente :
– le loyer et les charges échus, soit la somme correspondant à l’échéance du {{echeance}} et, le cas échéant, les arriérés ;
– pour un montant total à jour de : {{loyer}} € (loyer et charges), sous réserve de régularisation selon l’état exact de votre compte.

À défaut de paiement intégral dans ce délai, il sera procédé à la résiliation du bail aux torts du locataire et à l’engagement de toutes procédures utiles, sans autre avis.

Fait à _______________, le {{echeance}}

{{nom_bailleur}}
Pour accepter les présentes,
Signature du bailleur (ou mandataire)""",
    ),
    (
        LetterTemplate.RESILIATION,
        "Notification de résiliation de bail",
        """Objet : Résiliation du bail – logement situé {{adresse}}

{{bien}}

Madame, Monsieur {{locataire}},

Je vous informe par la présente de la résiliation du bail qui nous lie, concernant le logement désigné ci-dessus, aux conditions et dates prévues par le bail et la réglementation en vigueur.

Les modalités de sortie des lieux (état des lieux, restitution des clés, régularisation des charges) devront être convenues dans les meilleurs délais.

Je vous prie de croire, Madame, Monsieur, en l’assurance de mes salutations distinguées.

{{nom_bailleur}}
Date : {{echeance}}""",
    ),
    (
        LetterTemplate.AVENANT,
        "Avenant au bail d’habitation",
        """AVENANT AU CONTRAT DE LOCATION

Entre les soussignés :

Le bailleur : {{nom_bailleur}}

Et le locataire : {{locataire}}

Il a été convenu ce qui suit concernant le logement sis {{adresse}} ({{bien}}) :

Article 1 – Objet
Le présent avenant modifie le bail en date du {{echeance}} uniquement sur les points ci-après ; les autres clauses demeurent inchangées.

Article 2 – Modification(s)
[À compléter : nouveau loyer, durée, travaux, colocation, etc.]
– Loyer mensuel : {{loyer}} € charges comprises ou hors charges (préciser).

Article 3 – Entrée en vigueur
Le présent avenant prend effet à compter de sa signature par les deux parties.

Fait à _______________, le {{echeance}}, en deux exemplaires originaux.

Le bailleur                                        Le locataire
{{nom_bailleur}}                                  {{locataire}}""",
    ),
    (
        LetterTemplate.PREAVIS,
        "Lettre de préavis de départ (locataire / bailleur selon le cas)",
        """Objet : Préavis de départ – logement {{bien}}

{{adresse}}

Madame, Monsieur {{locataire}},

Conformément au bail et au Code de la construction et de l’habitation, je vous informe de mon intention de mettre fin au bail par un préavis de :

[ ] trois mois (zone tendue, sous réserve de dispositions locales ou du bail)
[ ] un mois (meublé, sous réserve)
[ ] autre durée : _______________

Le préavis court à compter de la réception de la présente (ou selon stipulation du bail).

Je vous propose de convenir d’un rendez-vous pour l’état des lieux de sortie et la restitution des clés.

Fait à _______________, le {{echeance}}

{{nom_bailleur}}
Signature""",
    ),
]


def ensure_default_letter_templates(user: User) -> int:
    """
    Crée les quatre modèles de lettre types s’ils manquent pour cet utilisateur.
    Retourne le nombre de modèles créés.
    """
    org = get_or_create_default_organisation(user)
    created = 0
    for letter_type, name, content in _DEFAULTS:
        if LetterTemplate.objects.filter(owner=user, letter_type=letter_type).exists():
            continue
        LetterTemplate.objects.create(
            owner=user,
            organisation=org,
            name=name,
            letter_type=letter_type,
            content=content,
        )
        created += 1
    return created
