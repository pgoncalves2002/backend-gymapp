"""
Camada fina sobre a REST API do Asaas (https://docs.asaas.com).

Objetivos:
  - **Modo scaffold**: enquanto não há `ASAAS_API_KEY` configurada, o app sobe
    normal e o fluxo GRÁTIS funciona 100%. Só os endpoints que falam com o
    Asaas respondem 503 claro.
  - **Sem SDK oficial** — o Asaas não tem cliente Python first-party. Usamos
    `httpx` com timeout curto e levantamos `AsaasError` em falha.
  - **Reaproveitável pelo split** (`payments/`): o gateway recebe a API key
    opcionalmente, então a mesma função serve pra plataforma (key da master)
    ou pra uma subconta (key White Label do personal).

Doc: https://docs.asaas.com/reference/sobre-a-api · Base sandbox:
`https://sandbox.asaas.com/api/v3` (override via env `ASAAS_API_BASE`).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from rest_framework.exceptions import APIException

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)


class AsaasNotConfigured(APIException):
    status_code = 503
    default_detail = (
        "Pagamento ainda não está configurado neste ambiente. "
        "Configure a chave do Asaas pra ativar a cobrança."
    )
    default_code = "asaas_not_configured"


class AsaasError(APIException):
    """Erro reportado pela API do Asaas (4xx/5xx)."""

    status_code = 502
    default_detail = "Erro na comunicação com o Asaas."
    default_code = "asaas_error"


def asaas_enabled() -> bool:
    """True quando há API key configurada (master da plataforma)."""
    return bool(getattr(settings, "ASAAS_API_KEY", ""))


def asaas_base_url() -> str:
    return getattr(settings, "ASAAS_API_BASE", "https://sandbox.asaas.com/api/v3").rstrip("/")


def _headers(api_key: str | None) -> dict[str, str]:
    key = api_key or getattr(settings, "ASAAS_API_KEY", "")
    if not key:
        raise AsaasNotConfigured()
    return {
        "access_token": key,
        "User-Agent": "FichaGym/1.0 (+https://fichagym.com)",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Faz uma chamada HTTP autenticada à API do Asaas.

    `api_key` opcional: se passado, usa a key de uma subconta (split White
    Label); senão, usa a `ASAAS_API_KEY` master da plataforma.

    Levanta:
      - `AsaasNotConfigured` (503) — sem key configurada (modo scaffold).
      - `AsaasError` (502) — Asaas devolveu erro (com `detail` extraído).
    """
    url = f"{asaas_base_url()}{path}"
    headers = _headers(api_key)
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.request(method, url, headers=headers, json=json, params=params)
    except httpx.HTTPError as exc:
        log.exception("Falha de rede contactando o Asaas (%s %s)", method, path)
        raise AsaasError(f"Falha de rede contactando o Asaas: {exc}") from exc

    if 200 <= resp.status_code < 300:
        if resp.content:
            return resp.json()
        return {}

    # Erro do Asaas — o corpo costuma vir como
    # {"errors": [{"code": "...", "description": "..."}]}.
    detail = _extract_error_detail(resp)
    log.warning(
        "Asaas %s %s devolveu %s: %s", method, path, resp.status_code, detail
    )
    err = AsaasError(detail)
    err.status_code = 502 if resp.status_code >= 500 else 400
    raise err


def _extract_error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text or f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(
                str(e.get("description") or e.get("code") or e) for e in errors
            )
        if body.get("errorDescription"):
            return str(body["errorDescription"])
    return resp.text or f"HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# Helpers de alto nível pra Customer / Subscription / Payment / Refund
# ---------------------------------------------------------------------------
def ensure_enabled() -> None:
    """Levanta 503 se o Asaas não está configurado (chamar no topo das views)."""
    if not asaas_enabled():
        raise AsaasNotConfigured()


def create_customer(
    *,
    name: str,
    email: str | None,
    cpf_cnpj: str | None = None,
    mobile_phone: str | None = None,
    external_reference: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST /v3/customers — cria um cliente (do Asaas).

    `external_reference` ajuda a achar o User pelo metadata depois.
    """
    payload: dict[str, Any] = {"name": name}
    if email:
        payload["email"] = email
    if cpf_cnpj:
        payload["cpfCnpj"] = cpf_cnpj
    if mobile_phone:
        payload["mobilePhone"] = mobile_phone
    if external_reference:
        payload["externalReference"] = external_reference
    return request("POST", "/customers", json=payload, api_key=api_key)


def create_subscription(
    *,
    customer_id: str,
    value_reais: float,
    cycle: str,
    next_due_date: str,
    billing_type: str = "UNDEFINED",
    description: str | None = None,
    external_reference: str | None = None,
    split: list[dict[str, Any]] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    POST /v3/subscriptions — assinatura recorrente do Asaas.

    `cycle` ∈ {"WEEKLY","BIWEEKLY","MONTHLY","QUARTERLY","SEMIANNUALLY","YEARLY"}.
    `billing_type` ∈ {"BOLETO","CREDIT_CARD","PIX","UNDEFINED"}. UNDEFINED deixa
    o pagador escolher no checkout hospedado.

    `split` opcional: lista de `{walletId, percentualValue}` ou
    `{walletId, fixedValue}` — a comissão da plataforma sai ANTES do rateio.
    """
    payload: dict[str, Any] = {
        "customer": customer_id,
        "billingType": billing_type,
        "value": value_reais,
        "cycle": cycle,
        "nextDueDate": next_due_date,
    }
    if description:
        payload["description"] = description
    if external_reference:
        payload["externalReference"] = external_reference
    if split:
        payload["split"] = split
    return request("POST", "/subscriptions", json=payload, api_key=api_key)


def get_subscription(subscription_id: str, api_key: str | None = None) -> dict[str, Any]:
    return request("GET", f"/subscriptions/{subscription_id}", api_key=api_key)


def cancel_subscription(subscription_id: str, api_key: str | None = None) -> dict[str, Any]:
    return request("DELETE", f"/subscriptions/{subscription_id}", api_key=api_key)


def create_payment(
    *,
    customer_id: str,
    value_reais: float,
    due_date: str,
    billing_type: str = "UNDEFINED",
    description: str | None = None,
    external_reference: str | None = None,
    split: list[dict[str, Any]] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST /v3/payments — cobrança avulsa (one-off)."""
    payload: dict[str, Any] = {
        "customer": customer_id,
        "billingType": billing_type,
        "value": value_reais,
        "dueDate": due_date,
    }
    if description:
        payload["description"] = description
    if external_reference:
        payload["externalReference"] = external_reference
    if split:
        payload["split"] = split
    return request("POST", "/payments", json=payload, api_key=api_key)


def refund_payment(
    payment_id: str,
    *,
    value_reais: float | None = None,
    split_refunds: list[dict[str, Any]] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    POST /v3/payments/{id}/refund — estorno total ou parcial.

    Estorno TOTAL (sem `value_reais`) reverte os splits automaticamente
    (inclui a comissão da plataforma). Estorno PARCIAL exige `split_refunds`
    pra dizer quem devolve quanto. Taxas do Asaas NÃO são devolvidas.
    """
    payload: dict[str, Any] = {}
    if value_reais is not None:
        payload["value"] = value_reais
    if split_refunds:
        payload["splitRefunds"] = split_refunds
    return request(
        "POST", f"/payments/{payment_id}/refund", json=payload or None, api_key=api_key
    )


def create_subaccount(
    *,
    name: str,
    email: str,
    cpf_cnpj: str,
    mobile_phone: str | None = None,
    address: dict[str, Any] | None = None,
    company_type: str | None = None,
    birth_date: str | None = None,
    income_value: float | None = None,
    incoming_transfer_pix_key: str | None = None,
) -> dict[str, Any]:
    """
    POST /v3/accounts — cria uma subconta Asaas pra um personal (split).

    Resposta inclui `id`, `walletId` (usado no array `split` das cobranças) e
    `apiKey` (usada se o personal for operar em White Label). Sandbox: máx
    **20 subcontas/dia**.

    Mínimos pedidos pela doc: name, email, cpfCnpj, mobilePhone, address,
    incomeValue (renda anual estimada pra CPF / faturamento anual pra CNPJ).
    """
    payload: dict[str, Any] = {
        "name": name,
        "email": email,
        "cpfCnpj": cpf_cnpj,
    }
    if mobile_phone:
        payload["mobilePhone"] = mobile_phone
    if birth_date:
        payload["birthDate"] = birth_date
    if company_type:
        payload["companyType"] = company_type
    if income_value is not None:
        payload["incomeValue"] = income_value
    if address:
        payload.update(address)
    if incoming_transfer_pix_key:
        payload["incomingTransferPixKey"] = incoming_transfer_pix_key
    return request("POST", "/accounts", json=payload)


def get_subaccount(account_id: str) -> dict[str, Any]:
    return request("GET", f"/accounts/{account_id}")


def get_balance(*, api_key: str | None = None) -> dict[str, Any]:
    """GET /v3/finance/balance — saldo da conta. `api_key` = subconta."""
    return request("GET", "/finance/balance", api_key=api_key)


def list_payments(
    *,
    status_in: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    GET /v3/payments — lista de cobranças. Sem `status_in` traz todas.
    Status válidos: PENDING, RECEIVED, CONFIRMED, OVERDUE, REFUNDED,
    RECEIVED_IN_CASH, DUNNING_REQUESTED, DUNNING_RECEIVED, AWAITING_RISK_ANALYSIS.
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status_in:
        # A API aceita repetir o parâmetro, mas o nosso `request` passa via
        # httpx (que aceita lista). Se houver só um status, manda string simples.
        params["status"] = status_in if len(status_in) > 1 else status_in[0]
    return request("GET", "/payments", params=params, api_key=api_key)


def create_transfer(
    *,
    value_reais: float,
    pix_address_key: str | None = None,
    pix_address_key_type: str | None = None,
    bank_account: dict[str, Any] | None = None,
    description: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    POST /v3/transfers — saque pra chave Pix (rápido) ou conta bancária (TED).

    Pix:  passar `pix_address_key` (+ opcionalmente `pix_address_key_type`,
          que aceita "CPF", "CNPJ", "EMAIL", "PHONE", "EVP").
    TED:  passar `bank_account` com {bank: {code}, ownerName, cpfCnpj,
          agency, account, accountDigit, bankAccountType}.
    """
    payload: dict[str, Any] = {"value": value_reais}
    if pix_address_key:
        payload["pixAddressKey"] = pix_address_key
        if pix_address_key_type:
            payload["pixAddressKeyType"] = pix_address_key_type
        payload["operationType"] = "PIX"
    elif bank_account:
        payload["bankAccount"] = bank_account
        payload["operationType"] = "TED"
    if description:
        payload["description"] = description
    return request("POST", "/transfers", json=payload, api_key=api_key)


def list_transfers(
    *, limit: int = 50, offset: int = 0, api_key: str | None = None
) -> dict[str, Any]:
    """GET /v3/transfers — lista de saques já feitos."""
    return request(
        "GET",
        "/transfers",
        params={"limit": limit, "offset": offset},
        api_key=api_key,
    )
