"""
Analyse de rentabilité des biens (Premium).
Génère une analyse par bien : indicateurs + texte IA (OpenAI si configuré) ou analyse par règles.
"""
from __future__ import annotations

from decimal import Decimal
from django.conf import settings


def get_property_metrics(property_obj):
    """
    Calcule les indicateurs de rentabilité pour un bien.
    Retourne un dict : rent_monthly, charges_tenant_monthly, mortgage_monthly, charges_property_monthly,
    cashflow_monthly, annual_rent, yield_pct (si prix d'achat), has_active_lease.
    """
    lease = (
        property_obj.leases.filter(is_active=True, archived_at__isnull=True)
        .order_by("-start_date")
        .first()
    )
    rent_monthly = Decimal(0)
    charges_tenant_monthly = Decimal(0)
    if lease:
        rent_monthly = lease.rent or Decimal(0)
        charges_tenant_monthly = lease.charges or Decimal(0)
    mortgage_monthly = property_obj.monthly_mortgage or Decimal(0)
    charges_property_monthly = property_obj.monthly_charges or Decimal(0)
    cashflow_monthly = rent_monthly + charges_tenant_monthly - mortgage_monthly - charges_property_monthly
    annual_rent = rent_monthly * 12
    yield_pct = None
    purchase_price = property_obj.purchase_price
    if purchase_price and purchase_price > 0 and annual_rent:
        yield_pct = float(annual_rent) / float(purchase_price) * 100
    return {
        "rent_monthly": rent_monthly,
        "charges_tenant_monthly": charges_tenant_monthly,
        "mortgage_monthly": mortgage_monthly,
        "charges_property_monthly": charges_property_monthly,
        "cashflow_monthly": cashflow_monthly,
        "annual_rent": annual_rent,
        "yield_pct": round(yield_pct, 1) if yield_pct is not None else None,
        "has_active_lease": lease is not None,
    }


def _rule_based_analysis(property_obj, metrics):
    """Génère une analyse en français à partir des indicateurs (sans API)."""
    name = property_obj.name
    cf = float(metrics["cashflow_monthly"])
    rent = float(metrics["rent_monthly"])
    yield_pct = metrics.get("yield_pct")
    has_lease = metrics["has_active_lease"]

    if not has_lease:
        return (
            f"{name} : Aucun bail actif. Complétez les informations (loyer, charges, crédit) et associez un bail actif "
            "pour obtenir une analyse de rentabilité."
        )
    parts = [f"{name} : "]
    if cf > 0:
        parts.append(f"Cashflow mensuel positif de {cf:.0f} €. Le loyer couvre les charges et le crédit.")
    elif cf < 0:
        parts.append(f"Cashflow mensuel négatif ({cf:.0f} €). Les revenus ne couvrent pas l'ensemble des charges et du crédit.")
    else:
        parts.append("Équilibre à l'euro près (cashflow nul).")
    if yield_pct is not None:
        if yield_pct >= 6:
            parts.append(f" Rendement brut d'environ {yield_pct} %, au-dessus de la moyenne.")
        elif yield_pct >= 4:
            parts.append(f" Rendement brut d'environ {yield_pct} %.")
        else:
            parts.append(f" Rendement brut d'environ {yield_pct} %. Pensez à vérifier le prix d'achat ou le niveau de loyer.")
    parts.append(" Ces indicateurs sont indicatifs ; consultez un professionnel pour des décisions d'investissement.")
    return " ".join(parts)


def _openai_analysis(property_obj, metrics):
    """Appelle l'API OpenAI pour une analyse en français (si clé configurée)."""
    api_key = getattr(settings, "OPENAI_API_KEY", None)
    if not api_key or not api_key.strip():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
        context = (
            f"Bien : {property_obj.name}. "
            f"Loyer mensuel : {metrics['rent_monthly']} €. "
            f"Charges locataire (mensuelles) : {metrics['charges_tenant_monthly']} €. "
            f"Mensualité de crédit : {metrics['mortgage_monthly']} €. "
            f"Charges du bien (mensuelles) : {metrics['charges_property_monthly']} €. "
            f"Cashflow mensuel : {metrics['cashflow_monthly']} €. "
        )
        if metrics.get("yield_pct") is not None:
            context += f"Rendement brut estimé : {metrics['yield_pct']} %. "
        context += "Bail actif : oui." if metrics["has_active_lease"] else "Bail actif : non."
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Tu es un assistant expert en immobilier locatif. Réponds en français, en 2 à 4 phrases courtes. Donne une analyse de rentabilité factuelle et des recommandations simples. Pas de liste à puces.",
                },
                {"role": "user", "content": f"Analyse la rentabilité de ce bien locatif : {context}"},
            ],
            max_tokens=300,
        )
        text = response.choices[0].message.content.strip() if response.choices else ""
        return text if text else None
    except Exception:
        return None


def get_rentability_analysis(property_obj, metrics):
    """
    Retourne un texte d'analyse pour le bien (IA si OPENAI_API_KEY, sinon analyse par règles).
    """
    text = _openai_analysis(property_obj, metrics)
    if text:
        return text
    return _rule_based_analysis(property_obj, metrics)
