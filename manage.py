#!/usr/bin/env python
import os
import sys

# Charger les variables du fichier .env (OPENAI_API_KEY, etc.) si présent
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main() -> None:
    """Point d'entrée du projet Django."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "immo_saas.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Impossible d'importer Django. Assurez-vous qu'il est installé "
            "et disponible dans votre environnement virtuel."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

