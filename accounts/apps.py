from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Contas"

    def ready(self) -> None:
        # Importa pra registrar os signal handlers (post_save de User).
        from . import signals  # noqa: F401
