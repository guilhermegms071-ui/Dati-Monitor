"""Downloads (Fase 8): publicação de instaladores pelo superadmin, a lista com a versão oferecida, retirada,
download no portal, links de instalação e o link público válido só com código de cadastro vigente."""

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AgentEnrollmentCode
from tests.agent_helpers import create_agent
from tests.conftest import Factory, auth, login


async def publish(
    client: httpx.AsyncClient, token: str, kind: str, arch: str, version: str, filename: str, body: bytes
) -> httpx.Response:
    return await client.post(
        "/api/v1/installers",
        params={"kind": kind, "arch": arch, "version": version, "filename": filename},
        content=body,
        headers={**auth(token), "Content-Type": "application/octet-stream"},
    )


async def test_publish_list_withdraw_and_download(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant = await factory.tenant(admin_role="superadmin")
    root = await login(client, tenant.admin_email)
    ok = await publish(client, root, "windows", "all", "1.0.0", "dati-monitor-setup-1.0.0.exe", b"MZ setup 1")
    assert ok.status_code == 201, ok.text
    assert ok.json()["sha256"] == hashlib.sha256(b"MZ setup 1").hexdigest()
    newer = await publish(
        client, root, "windows", "all", "1.1.0", "dati-monitor-setup-1.1.0.exe", b"MZ setup 2"
    )
    deb = await publish(
        client, root, "deb", "arm", "1.1.0", "dati-monitor-agent_1.1.0_armhf.deb", b"!<arch>\n"
    )
    assert (newer.status_code, deb.status_code) == (201, 201)

    dup = await publish(client, root, "windows", "all", "1.1.0", "outro.exe", b"x")
    assert dup.json()["detail"]["code"] == "installer_exists"
    for args, code in (
        (("windows", "amd64", "1.2.0", "s.exe"), "invalid_arch"),
        (("deb", "all", "1.2.0", "a.deb"), "invalid_arch"),
        (("deb", "arm", "1.2", "a.deb"), "invalid_version"),
        (("tar", "arm", "1.2.0", "a.zip"), "invalid_filename"),
    ):
        bad = await publish(client, root, *args, b"x")
        assert bad.json()["detail"]["code"] == code, args

    listed = {
        (i["kind"], i["version"]): i
        for i in (await client.get("/api/v1/installers", headers=auth(root))).json()
    }
    assert listed[("windows", "1.1.0")]["latest"] is True
    assert listed[("windows", "1.0.0")]["latest"] is False
    # Retirar a 1.1.0 volta a oferecer a 1.0.0.
    wid = listed[("windows", "1.1.0")]["id"]
    assert (
        await client.patch(f"/api/v1/installers/{wid}", json={"withdrawn": True}, headers=auth(root))
    ).status_code == 204
    listed = {
        (i["kind"], i["version"]): i
        for i in (await client.get("/api/v1/installers", headers=auth(root))).json()
    }
    assert listed[("windows", "1.0.0")]["latest"] is True

    file = await client.get(
        f"/api/v1/installers/{listed[('windows', '1.0.0')]['id']}/file", headers=auth(root)
    )
    assert file.content == b"MZ setup 1"
    assert "dati-monitor-setup-1.0.0.exe" in file.headers["content-disposition"]

    # Só o superadmin publica; técnico vê a lista.
    other = await factory.tenant("Outra")
    admin = await login(client, other.admin_email)
    assert (await publish(client, admin, "windows", "all", "2.0.0", "s.exe", b"x")).status_code == 403
    _, tech_email = await factory.user(other.reseller_id, role="technician")
    tech = await login(client, tech_email)
    assert len((await client.get("/api/v1/installers", headers=auth(tech))).json()) == 3


async def test_public_links_need_a_valid_enrollment_code(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant(admin_role="superadmin")
    root = await login(client, tenant.admin_email)
    await publish(client, root, "windows", "all", "1.0.0", "dati-monitor-setup-1.0.0.exe", b"MZ setup")
    await publish(
        client, root, "deb", "arm64", "1.0.0", "dati-monitor-agent_1.0.0_arm64.deb", b"!<arch>\ndeb"
    )
    await publish(
        client, root, "tar", "arm64", "1.0.0", "dati-monitor-agent-1.0.0-linux-arm64.tar.gz", b"tgz"
    )
    created = await create_agent(client, root, tenant.site_id)
    code = created["enrollment"]["code"]

    enrollment = created["enrollment"]
    assert enrollment["windows_url"].endswith(f"/api/public/installer?code={code}&platform=windows")
    assert f"/CODE={code}" in enrollment["windows_silent"]
    assert enrollment["linux_command"].startswith("curl -fsSL ")
    assert "conferido na hora" in enrollment["instructions"][1]

    win = await client.get("/api/public/installer", params={"code": code, "platform": "windows"})
    assert win.status_code == 200
    assert win.content == b"MZ setup"
    deb = await client.get(
        "/api/public/installer", params={"code": code, "platform": "linux", "arch": "arm64", "format": "deb"}
    )
    assert deb.content == b"!<arch>\ndeb"
    tgz = await client.get(
        "/api/public/installer", params={"code": code, "platform": "linux", "arch": "arm64", "format": "tar"}
    )
    assert tgz.content == b"tgz"
    none = await client.get(
        "/api/public/installer", params={"code": code, "platform": "linux", "arch": "386", "format": "deb"}
    )
    assert none.status_code == 404

    script = await client.get("/api/public/install.sh", params={"code": code})
    assert script.status_code == 200
    assert f'CODE="{code}"' in script.text
    assert 'SERVER="http' in script.text
    assert "@SERVER@" not in script.text
    assert "DatiMonitorAgent" in script.text

    # Código inexistente, expirado ou já usado: nada sai.
    assert (await client.get("/api/public/installer", params={"code": "ZZZZ9999"})).status_code == 404
    async with sessionmaker() as s:
        await s.execute(
            update(AgentEnrollmentCode).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    assert (await client.get("/api/public/installer", params={"code": code})).status_code == 404
    assert (await client.get("/api/public/install.sh", params={"code": code})).status_code == 404
