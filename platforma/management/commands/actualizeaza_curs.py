"""Actualizează cursul EUR de la BNR. Rulare zilnică (cron): python manage.py actualizeaza_curs
Pentru istoric: python manage.py actualizeaza_curs --an 2025"""
from django.core.management.base import BaseCommand

from platforma.curs import actualizeaza


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--an", type=int)

    def handle(self, an=None, **kw):
        self.stdout.write(f"Cursuri importate: {actualizeaza(an)}")
