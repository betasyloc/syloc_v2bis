"""
Commande à lancer quotidiennement (cron) pour envoyer les relances impayés par email :
paliers J+7, J+15 et J+30 (après l'échéance), configurables via ReminderRule par utilisateur.

Exemple cron (tous les jours à 8h) :
    0 8 * * * cd /chemin/immo_saas && .venv/bin/python manage.py send_rent_reminders
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from rental.emails import send_late_rent_reminder, _lease_tenant_emails
from rental.models import ReminderRule, RentInvoice
from rental.reminder_defaults import ensure_default_reminder_rules


def _effective_last_tier(invoice: RentInvoice) -> int | None:
    """Dernier palier J+N déjà envoyé, avec reprise des anciennes données (sans champ)."""
    if invoice.late_reminder_last_days_after_due is not None:
        return int(invoice.late_reminder_last_days_after_due)
    # Ancien système (relances à J+5 puis J+15) : si au moins une relance a été envoyée
    # sans ce champ, on considère que le premier palier a été couvert → suite logique J+15 puis J+30.
    if invoice.last_late_reminder_sent_at is not None:
        return 7
    return None


def _next_rule_to_send(
    rules: list[ReminderRule],
    days_late: int,
    last_tier: int | None,
) -> ReminderRule | None:
    """Première règle active, par ordre croissant de J+N, pas encore envoyée et déjà atteinte."""
    candidates = [
        r
        for r in rules
        if r.is_active
        and days_late >= r.days_after_due
        and (last_tier is None or r.days_after_due > last_tier)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.days_after_due)


class Command(BaseCommand):
    help = "Envoie les relances impayés par email (paliers J+7, J+15, J+30 par défaut)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait envoyé sans envoyer d'email",
        )

    def handle(self, *args, **options):
        now = timezone.now().date()
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("Mode dry-run : aucun email ne sera envoyé.")

        late_invoices = RentInvoice.objects.filter(
            status__in=["DUE", "LATE"],
            due_date__lt=now,
        ).select_related("lease", "lease__property", "lease__property__owner").prefetch_related(
            "lease__tenants"
        )

        owners_done: set[int] = set()

        for invoice in late_invoices:
            if not _lease_tenant_emails(invoice.lease):
                continue

            owner = invoice.lease.property.owner
            if owner.pk not in owners_done:
                ensure_default_reminder_rules(owner)
                owners_done.add(owner.pk)

            rules = list(
                ReminderRule.objects.filter(owner=owner, is_active=True).order_by("days_after_due")
            )
            if not rules:
                continue

            days_late = (now - invoice.due_date).days
            last_tier = _effective_last_tier(invoice)
            next_rule = _next_rule_to_send(rules, days_late, last_tier)
            if next_rule is None:
                continue

            rank = 1 + sum(1 for r in rules if r.days_after_due < next_rule.days_after_due and r.is_active)

            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Relance J+{next_rule.days_after_due} : "
                    f"{_lease_tenant_emails(invoice.lease)} - {invoice}"
                )
                continue

            if send_late_rent_reminder(
                invoice,
                days_after_due=next_rule.days_after_due,
                reminder_rank=rank,
                rule=next_rule,
            ):
                invoice.last_late_reminder_sent_at = timezone.now()
                invoice.late_reminder_last_days_after_due = next_rule.days_after_due
                invoice.status = "LATE"
                invoice.save(
                    update_fields=[
                        "last_late_reminder_sent_at",
                        "late_reminder_last_days_after_due",
                        "status",
                    ]
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Relance J+{next_rule.days_after_due} envoyée : {invoice}"
                    )
                )

        self.stdout.write(self.style.SUCCESS("Envoi des relances terminé."))
