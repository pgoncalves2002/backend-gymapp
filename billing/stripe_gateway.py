"""
Camada fina sobre a lib `stripe`.

Objetivos:
  - **Modo scaffold**: enquanto não há `STRIPE_SECRET_KEY` configurada (ou a
    lib `stripe` nem instalada), o app sobe normal e o fluxo GRÁTIS funciona
    100%. Só os endpoints que realmente falam com a Stripe respondem com erro
    claro (503).
  - **Import lazy**: `import stripe` acontece dentro das funções, não no topo
    do módulo — assim importar este arquivo nunca quebra o boot.
"""

from django.conf import settings
from rest_framework.exceptions import APIException


class StripeNotConfigured(APIException):
    status_code = 503
    default_detail = (
        "Pagamento ainda não está configurado neste ambiente. "
        "Configure as chaves da Stripe pra ativar a assinatura."
    )
    default_code = "stripe_not_configured"


def stripe_enabled() -> bool:
    """True só quando há secret key configurada."""
    return bool(getattr(settings, "STRIPE_SECRET_KEY", ""))


def get_stripe():
    """
    Retorna o módulo `stripe` já com a api_key setada.

    Levanta StripeNotConfigured (503) se a key não está setada ou a lib não
    está instalada — mensagem clara em vez de 500.
    """
    if not stripe_enabled():
        raise StripeNotConfigured()
    try:
        import stripe  # import lazy: não quebra o boot em modo scaffold
    except ImportError as exc:  # pragma: no cover
        raise StripeNotConfigured(
            "A biblioteca 'stripe' não está instalada no ambiente."
        ) from exc
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def price_id_for_plan(plan: str) -> str:
    """
    Mapeia "monthly"/"annual" pro Price ID REAL definido em settings.

    Importante: o price NUNCA vem do cliente — sempre resolvido aqui no
    servidor, pra ninguém forjar o valor da cobrança.
    """
    prices = getattr(settings, "STRIPE_PRICES", {})
    price = prices.get(plan)
    if not price:
        raise StripeNotConfigured(
            f"Price da Stripe não configurado para o plano '{plan}'."
        )
    return price
