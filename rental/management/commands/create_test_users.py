"""
Crée 5 comptes utilisateur pour des tests (bêta-testeurs).
Usage : python manage.py create_test_users
        python manage.py create_test_users --count 10
Les comptes sont créés avec un mot de passe commun (à changer après première connexion).
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Crée des comptes test pour faire tester l'application par plusieurs utilisateurs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=5,
            help="Nombre de comptes à créer (défaut : 5)",
        )
        parser.add_argument(
            "--password",
            type=str,
            default="TestSyloc2025!",
            help="Mot de passe commun pour tous les comptes (défaut : TestSyloc2025!)",
        )
        parser.add_argument(
            "--prefix",
            type=str,
            default="testeur",
            help="Préfixe des identifiants (défaut : testeur → testeur1@test.syloc.local, ...)",
        )

    def handle(self, *args, **options):
        count = options["count"]
        password = options["password"]
        prefix = options["prefix"]
        User = get_user_model()

        created = []
        for i in range(1, count + 1):
            email = f"{prefix}{i}@test.syloc.local"
            username = email
            if User.objects.filter(email__iexact=email).exists():
                self.stdout.write(self.style.WARNING(f"Compte déjà existant : {email}"))
                continue
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
            created.append(email)
            self.stdout.write(self.style.SUCCESS(f"Créé : {email}"))

        if created:
            self.stdout.write("")
            self.stdout.write("Identifiants à transmettre aux testeurs :")
            self.stdout.write(f"  URL : celle de votre application (ex. https://votre-app.onrender.com)")
            self.stdout.write(f"  Mot de passe commun : {password}")
            self.stdout.write("  Comptes :")
            for email in created:
                self.stdout.write(f"    - {email}")
            self.stdout.write("")
            self.stdout.write("Chaque testeur se connecte avec son email et ce mot de passe.")
        else:
            self.stdout.write("Aucun nouveau compte créé (tous existaient déjà).")
