"""Authentification par email en plus du username."""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()


class EmailAuthBackend(ModelBackend):
    """Permet de se connecter avec l'email ou le username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        username = (username or "").strip()
        if not username:
            return None
        # Identifiant : insensible à la casse (évite les échecs si majuscules différentes)
        user = User.objects.filter(username__iexact=username).first()
        if user is None and "@" in username:
            # Email : insensible à la casse (SQLite / comportements variables selon la BDD)
            user = User.objects.filter(email__iexact=username).first()
        if user is None:
            return None
        if user.check_password(password):
            return user
        return None
