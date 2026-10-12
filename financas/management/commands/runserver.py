from django.contrib.staticfiles.management.commands.runserver import Command as RunserverCommand


class Command(RunserverCommand):
    # a 8000 já é usada pelo Master Obra
    default_port = "8001"
