"""Chave própria por serviço: com as chaves públicas configuradas, um especialista não se passa
por outro, e o segredo compartilhado (HS256) deixa de valer."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.governanca.erros import AuthError
from app.governanca.identidade import verify_agent_token
from tests.conftest import agent_token


def _par():
    privada = Ed25519PrivateKey.generate()
    publica = privada.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    return privada, publica


@pytest.fixture
def com_chaves(settings, tmp_path):
    pasta = tmp_path / "publicas"
    pasta.mkdir()
    privadas = {}
    for servico in ("orquestrador", "extrator", "validador"):
        privadas[servico], publica = _par()
        (pasta / f"{servico}.pem").write_bytes(publica)
    return settings.model_copy(update={"chaves_publicas_dir": pasta, "mcp_agent_secret": ""}), privadas


def _token(privada, kid: str, agent: str, **extra) -> str:
    claims = {"agent": agent, "run_id": "exec-1", "usr": "ana", "aud": "mcp-server", "exp": int(time.time()) + 60,
              **extra}
    return jwt.encode(claims, privada, algorithm="EdDSA", headers={"kid": kid})


def test_cada_servico_fala_pelo_proprio_agente(com_chaves):
    settings, privadas = com_chaves
    assert verify_agent_token(_token(privadas["extrator"], "extrator", "extrator"), settings)["agent"] == "extrator"
    assert verify_agent_token(_token(privadas["orquestrador"], "orquestrador", "sistema"), settings)["agent"] == \
        "sistema"


def test_especialista_nao_se_passa_por_outro(com_chaves):
    settings, privadas = com_chaves
    with pytest.raises(AuthError) as erro:
        verify_agent_token(_token(privadas["validador"], "validador", "extrator"), settings)
    assert erro.value.status == 403


def test_kid_falso_nao_passa_na_assinatura(com_chaves):
    settings, privadas = com_chaves
    with pytest.raises(AuthError):
        verify_agent_token(_token(privadas["validador"], "extrator", "extrator"), settings)


def test_com_chaves_o_segredo_compartilhado_nao_vale(com_chaves):
    settings, _ = com_chaves
    with pytest.raises(AuthError):
        verify_agent_token(agent_token("extrator"), settings)


def test_servico_sem_chave_registrada_e_recusado(com_chaves):
    settings, _ = com_chaves
    estranha, _ = _par()
    with pytest.raises(AuthError):
        verify_agent_token(_token(estranha, "redator", "redator"), settings)


def test_servidor_sobe_sem_segredo_quando_ha_chaves(com_chaves):
    settings, _ = com_chaves
    assert settings.problem is None
