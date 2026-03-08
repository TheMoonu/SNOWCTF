from django.apps import AppConfig


class CompetitionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'competition'
    verbose_name = 'CTF竞赛'

    def ready(self):
        from . import signals