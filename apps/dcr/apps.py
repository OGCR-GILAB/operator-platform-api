from django.apps import AppConfig


class DcrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dcr"

    def ready(self):
        # registers the OpenAPI security scheme for DirectLogin
        from . import schema  # noqa: F401
