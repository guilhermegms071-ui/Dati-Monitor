"""Critério 22 da seção 15: toda lista do portal é paginada no servidor e nenhuma resposta da API contém
senha ou hash. Verificado no contrato (OpenAPI) e nas respostas reais."""

import re
from typing import Any

import httpx

from app.api.main import create_app
from tests.agent_helpers import enrolled_agent
from tests.conftest import Factory, auth, login

# GETs que devolvem lista sem cursor: cada uma é limitada por construção. Lista nova sem paginação faz este
# teste falhar até ser paginada ou justificada aqui.
BOUNDED_LISTS = {
    "/api/v1/agents/{agent_id}/heartbeats": "janela de horas, no máximo 5.000 pontos",
    "/api/v1/agents/{agent_id}/logs": "100 envios de log mais recentes do coletor",
    "/api/v1/agents/{agent_id}/versions": "uma linha por versão vista no coletor",
    "/api/v1/alert-rules": "regras fixas do produto (uma por tipo de alerta)",
    "/api/v1/computers/{agent_id}/usb-printers": "impressoras USB de um PC",
    "/api/v1/custom-fields": "campos personalizados da empresa (cadastro pequeno)",
    "/api/v1/devices/{device_id}/adjustments": "500 ajustes mais recentes de um equipamento",
    "/api/v1/devices/{device_id}/counters": "série diária/mensal de um equipamento, até 400 dias",
    "/api/v1/devices/{device_id}/supplies": "suprimentos atuais de um equipamento",
    "/api/v1/devices/{device_id}/supplies/history": "série de um equipamento, até 400 dias",
    "/api/v1/erp-tokens": "tokens da integração ERP (poucos, revogáveis)",
    "/api/v1/installers": "instaladores publicados (um por tipo/arquitetura/versão)",
    "/api/v1/mib-walks": "100 walks mais recentes",
    "/api/v1/notification-channels": "canais de notificação da empresa (cadastro pequeno)",
    "/api/v1/profiles": "perfis de marca (um por fabricante)",
    "/api/v1/releases": "versões publicadas do coletor",
    "/api/v1/reports": "catálogo fixo de relatórios",
    "/api/v1/roles": "papéis fixos do produto",
    "/api/v1/sites/map": "uma linha agregada por local (pontos do mapa), sem equipamentos",
    "/api/v1/sites/{site_id}/ip-ranges": "faixas de IP de um local",
    "/api/v1/sites/{site_id}/snmp-credentials": "credenciais SNMP de um local (sem os segredos)",
}

SENSITIVE = re.compile(r"(?i)password|passwd|hash|secret|private_key|totp")
# Campos de resposta com esses nomes que NÃO carregam senha nem hash (ou que são o próprio objetivo da rota).
ALLOWED_FIELDS = {
    "MeResponse.must_change_password": "booleano",
    "UserOut.must_change_password": "booleano",
    "MeResponse.totp_enabled": "booleano",
    "UserOut.totp_enabled": "booleano",
    "SnmpCredentialOut.has_auth_password": "booleano: só diz se existe",
    "SnmpCredentialOut.has_priv_password": "booleano: só diz se existe",
    "UserCreated.temporary_password": "senha temporária gerada, mostrada uma vez a quem criou o usuário",
    "ResetPasswordAdminResponse.temporary_password": "senha temporária gerada, mostrada uma vez",
    "TotpSetupResponse.secret": "segredo do 2FA mostrado uma vez para o QR code do próprio usuário",
    "EnrollResponse.secret": "credencial do coletor entregue uma vez no cadastro (canal do agente)",
    "CredentialConfig.v3_auth_password": "configuração do coletor autenticado (precisa da senha SNMPv3)",
    "CredentialConfig.v3_priv_password": "configuração do coletor autenticado (precisa da senha SNMPv3)",
}


def _response_schemas(spec: dict[str, Any]) -> set[str]:
    used: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    used.add(v.rsplit("/", 1)[-1])
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    for ops in spec["paths"].values():
        for op in ops.values():
            walk(op.get("responses", {}))
    schemas = spec["components"]["schemas"]
    size = -1
    while size != len(used):
        size = len(used)
        for name in list(used):
            walk(schemas.get(name, {}))
    return used


def test_every_list_is_paginated_or_bounded() -> None:
    spec = create_app(run_bootstrap=False).openapi()
    unpaged = set()
    for path, ops in spec["paths"].items():
        get = ops.get("get")
        if not get:
            continue
        for media in get.get("responses", {}).get("200", {}).get("content", {}).values():
            if media.get("schema", {}).get("type") == "array":
                unpaged.add(path)
    assert unpaged == set(BOUNDED_LISTS), (
        f"listas sem paginação não justificadas: {sorted(unpaged - set(BOUNDED_LISTS))}; "
        f"justificativas sem rota: {sorted(set(BOUNDED_LISTS) - unpaged)}"
    )


def test_no_response_schema_carries_password_or_hash() -> None:
    spec = create_app(run_bootstrap=False).openapi()
    schemas = spec["components"]["schemas"]
    found = {
        f"{name}.{prop}"
        for name in _response_schemas(spec)
        for prop in schemas.get(name, {}).get("properties", {})
        if SENSITIVE.search(prop)
    }
    assert found <= set(ALLOWED_FIELDS), (
        f"campos sensíveis em respostas: {sorted(found - set(ALLOWED_FIELDS))}"
    )


async def test_real_responses_never_contain_password_or_hash(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    await factory.user(tenant.reseller_id, role="customer_viewer")  # mais um hash no banco para a lista
    await enrolled_agent(client, tenant)
    cred = await client.post(
        f"/api/v1/sites/{tenant.site_id}/snmp-credentials",
        json={"version": "v2c", "community": "comunidade-secreta-123"},
        headers=auth(admin),
    )
    assert cred.status_code == 201, cred.text
    paths = [
        "/api/v1/auth/me",
        "/api/v1/users?limit=200",
        "/api/v1/audit?limit=200",
        "/api/v1/agents?limit=200",
        f"/api/v1/sites/{tenant.site_id}/snmp-credentials",
        f"/api/v1/sites/{tenant.site_id}",
        "/api/v1/erp-tokens",
        "/api/v1/notification-channels",
    ]
    for path in paths:
        resp = await client.get(path, headers=auth(admin))
        assert resp.status_code == 200, (path, resp.text)
        body = resp.text
        assert "$argon2" not in body, f"{path} devolveu um hash de senha"
        assert Factory.PASSWORD not in body, f"{path} devolveu a senha de um usuário"
        assert "comunidade-secreta-123" not in body, f"{path} devolveu a comunidade SNMP"
