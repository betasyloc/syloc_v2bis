"""
Exporte les données d'un utilisateur (sauvegarde / portabilité).
Usage : python manage.py export_user_data email@exemple.com
        python manage.py export_user_data --user 1
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from rental.models import (
    Tenant,
    Lease,
    RentInvoice,
    InspectionReport,
    properties_visible_to,
    tenants_visible_to,
    leases_visible_to,
)


def _serialize_decimal(d):
    return float(d) if d is not None else None


def _serialize_date(d):
    return d.isoformat() if d else None


def _serialize_datetime(d):
    return d.isoformat() if d else None


class Command(BaseCommand):
    help = "Exporte les données d'un utilisateur (biens, locataires, baux, loyers, états des lieux) en JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "identifier",
            nargs="?",
            type=str,
            help="Email ou nom d'utilisateur du compte à exporter",
        )
        parser.add_argument(
            "--user",
            type=int,
            dest="user_id",
            help="ID du compte utilisateur (alternative à l'email)",
        )
        parser.add_argument(
            "-o", "--output",
            type=str,
            dest="output",
            help="Fichier de sortie (défaut : export_syloc_<email>_<date>.json)",
        )

    def handle(self, *args, **options):
        identifier = options.get("identifier")
        user_id = options.get("user_id")
        output_path = options.get("output")

        User = get_user_model()
        if user_id:
            try:
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Utilisateur avec l'ID {user_id} introuvable."))
                return
        elif identifier:
            user = User.objects.filter(
                email__iexact=identifier.strip()
            ).first() or User.objects.filter(
                username__iexact=identifier.strip()
            ).first()
            if not user:
                self.stderr.write(self.style.ERROR(f"Aucun utilisateur trouvé pour : {identifier}"))
                return
        else:
            self.stderr.write(self.style.ERROR("Indiquez un email, un nom d'utilisateur ou --user <id>."))
            return

        now = timezone.now()
        date_suffix = now.strftime("%Y%m%d_%H%M")
        safe_email = "".join(c if c.isalnum() or c in "._-" else "_" for c in (user.email or user.username or "user"))
        default_filename = f"export_syloc_{safe_email}_{date_suffix}.json"
        out_path = output_path or default_filename

        properties = list(properties_visible_to(user).order_by("name").prefetch_related("works"))
        tenants = tenants_visible_to(user).order_by("last_name", "first_name")
        leases = leases_visible_to(user).select_related("property").prefetch_related("tenants").order_by("-start_date")
        invoices = RentInvoice.objects.filter(lease__property__in=properties_visible_to(user)).select_related("lease", "lease__property").order_by("due_date")
        inspections = InspectionReport.objects.filter(lease__property__in=properties_visible_to(user)).select_related("lease", "lease__property").order_by("-report_date")

        data = {
            "export_date": now.isoformat(),
            "user": {
                "id": user.pk,
                "email": user.email or "",
                "username": user.username,
            },
            "properties": [],
            "tenants": [],
            "leases": [],
            "rent_invoices": [],
            "inspection_reports": [],
            "property_works": [],
        }

        for p in properties:
            data["properties"].append({
                "id": p.pk,
                "name": p.name,
                "address": p.address or "",
                "city": p.city or "",
                "zip_code": p.zip_code or "",
                "owner_type": p.owner_type,
                "purchase_price": _serialize_decimal(p.purchase_price),
                "notary_fees": _serialize_decimal(p.notary_fees),
                "monthly_mortgage": _serialize_decimal(p.monthly_mortgage),
                "monthly_charges": _serialize_decimal(p.monthly_charges),
                "created_at": _serialize_datetime(p.created_at),
                "archived_at": _serialize_datetime(p.archived_at),
            })
            for w in p.works.all():
                data["property_works"].append({
                    "id": w.pk,
                    "property_id": p.pk,
                    "work_type": w.work_type,
                    "work_type_label": w.get_work_type_display(),
                    "title": w.title,
                    "description": w.description or "",
                    "work_date": _serialize_date(w.work_date),
                    "amount": _serialize_decimal(w.amount),
                    "created_at": _serialize_datetime(w.created_at),
                    "updated_at": _serialize_datetime(w.updated_at),
                })

        for t in tenants:
            data["tenants"].append({
                "id": t.pk,
                "first_name": t.first_name,
                "last_name": t.last_name,
                "email": t.email or "",
                "phone": t.phone or "",
                "created_at": _serialize_datetime(t.created_at),
                "archived_at": _serialize_datetime(t.archived_at),
            })

        for le in leases:
            tenant_ids = list(le.tenants.values_list("pk", flat=True))
            data["leases"].append({
                "id": le.pk,
                "property_id": le.property_id,
                "tenant_ids": tenant_ids,
                "start_date": _serialize_date(le.start_date),
                "end_date": _serialize_date(le.end_date),
                "rent": _serialize_decimal(le.rent),
                "charges": _serialize_decimal(le.charges),
                "deposit": _serialize_decimal(le.deposit),
                "is_active": le.is_active,
                "lease_document": le.lease_document or "",
                "attached_file": le.attached_file.name if getattr(le, "attached_file", None) and le.attached_file else "",
                "created_at": _serialize_datetime(le.created_at),
                "updated_at": _serialize_datetime(le.updated_at),
                "archived_at": _serialize_datetime(le.archived_at),
            })

        for inv in invoices:
            data["rent_invoices"].append({
                "id": inv.pk,
                "lease_id": inv.lease_id,
                "due_date": _serialize_date(inv.due_date),
                "amount_rent": _serialize_decimal(inv.amount_rent),
                "amount_charges": _serialize_decimal(inv.amount_charges),
                "status": inv.status,
                "paid_date": _serialize_date(inv.paid_date),
                "created_at": _serialize_datetime(inv.created_at),
            })

        for insp in inspections:
            data["inspection_reports"].append({
                "id": insp.pk,
                "lease_id": insp.lease_id,
                "report_type": insp.report_type,
                "report_date": _serialize_date(insp.report_date),
                "notes": insp.notes or "",
                "attached_file": insp.attached_file.name if getattr(insp, "attached_file", None) and insp.attached_file else "",
                "created_at": _serialize_datetime(insp.created_at),
            })

        import json
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Export enregistré : {out_path}"))
        self.stdout.write(
            f"  {len(data['properties'])} biens, {len(data['property_works'])} entrées travaux, "
            f"{len(data['tenants'])} locataires, "
            f"{len(data['leases'])} baux, {len(data['rent_invoices'])} loyers, "
            f"{len(data['inspection_reports'])} états des lieux."
        )
