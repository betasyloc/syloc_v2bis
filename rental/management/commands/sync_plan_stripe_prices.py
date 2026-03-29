"""Copie les STRIPE_PRICE_ID_* (settings / .env) vers les enregistrements Plan en base."""

from django.conf import settings
from django.core.management.base import BaseCommand

from rental.models import Plan


def _strip(s: str | None) -> str:
    return (s or "").strip()


class Command(BaseCommand):
    help = (
        "Met à jour les champs stripe_price_id / stripe_price_id_annual des offres « base » et « premium » "
        "à partir des variables STRIPE_PRICE_ID_* définies dans le .env (ou settings). "
        "À utiliser lorsque Stripe est à jour et que SyLoc doit refléter les mêmes price_…."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche les changements sans enregistrer en base.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        base = Plan.objects.filter(slug=Plan.BASE).first()
        prem = Plan.objects.filter(slug=Plan.PREMIUM).first()
        if not base or not prem:
            self.stderr.write(self.style.ERROR("Offres « base » ou « premium » introuvables en base."))
            return

        env_bm = _strip(getattr(settings, "STRIPE_PRICE_ID_BASE", None))
        env_ba = _strip(getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None))
        env_pm = _strip(getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None))
        env_pa = _strip(getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None))

        if not any((env_bm, env_ba, env_pm, env_pa)):
            self.stdout.write(
                self.style.WARNING(
                    "Aucune variable STRIPE_PRICE_ID_BASE(_ANNUAL) ni STRIPE_PRICE_ID_PREMIUM(_ANNUAL) "
                    "n’est renseignée. Remplissez le .env puis relancez cette commande, "
                    "ou éditez les offres dans l’admin Django."
                )
            )
            return

        self._sync_plan(
            base,
            env_bm,
            env_ba,
            "Basic",
            dry,
        )
        self._sync_plan(
            prem,
            env_pm,
            env_pa,
            "Premium",
            dry,
        )

    def _sync_plan(self, plan: Plan, env_m: str, env_a: str, label: str, dry: bool) -> None:
        updates: dict[str, str] = {}
        cur_m = _strip(plan.stripe_price_id)
        cur_a = _strip(plan.stripe_price_id_annual)

        effective_m = env_m or cur_m
        if env_m:
            updates["stripe_price_id"] = env_m
            self.stdout.write(f"  {label} mensuel -> {env_m}")
        if env_a:
            if env_a == effective_m:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {label} annuel ignoré (identique au mensuel effectif « {effective_m} »)."
                    )
                )
            else:
                updates["stripe_price_id_annual"] = env_a
                self.stdout.write(f"  {label} annuel -> {env_a}")

        if not updates:
            self.stdout.write(f"{label} : rien à mettre à jour depuis le .env.")
            return

        if dry:
            self.stdout.write(self.style.NOTICE(f"{label} (dry-run) : pas d’enregistrement."))
            return

        Plan.objects.filter(pk=plan.pk).update(**updates)
        self.stdout.write(self.style.SUCCESS(f"{label} : enregistré en base."))

