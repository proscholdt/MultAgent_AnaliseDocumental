"""Chave própria por serviço: os tokens saem EdDSA com o nome do serviço, e o especialista só
aceita o orquestrador pela chave pública dele."""
import jwt
import pytest

from app.ferramentas.cli import generate_keys
from app.nucleo.tokens import AuthError, Identity, a2a_headers, chave_propria, mcp_token, verify_a2a_token

QUEM = Identity(run_id="exec-1", user_id="ana", user_token="tok")


def _servico(settings, chaves, nome, destino):
    """O contêiner de um serviço: a própria privada e as públicas, como no compose."""
    raiz = destino / nome
    (raiz / "propria").mkdir(parents=True)
    (raiz / "propria" / f"{nome}.pem").write_bytes((chaves / nome / f"{nome}.pem").read_bytes())
    (raiz / "publicas").symlink_to(chaves / "publicas", target_is_directory=True)
    return settings.model_copy(update={"chaves_dir": raiz})


@pytest.fixture
def chaves(tmp_path):
    assert generate_keys(tmp_path / "chaves", ["orquestrador", "extrator"]) == 0
    return tmp_path / "chaves"


def test_sem_chave_vale_o_segredo_como_antes(settings):
    assert chave_propria(settings) is None
    assert jwt.get_unverified_header(mcp_token(settings, "extrator", QUEM))["alg"] == "HS256"


def test_com_chave_o_token_do_mcp_sai_assinado_pelo_servico(settings, chaves, tmp_path):
    extrator = _servico(settings, chaves, "extrator", tmp_path / "ctr")
    cabecalho = jwt.get_unverified_header(mcp_token(extrator, "extrator", QUEM))
    assert cabecalho == {"alg": "EdDSA", "kid": "extrator", "typ": "JWT"}
    assert extrator.problem is None or "MCP_AGENT_SECRET" not in extrator.problem


def test_especialista_aceita_so_o_orquestrador_pela_chave_publica(settings, chaves, tmp_path):
    orquestrador = _servico(settings, chaves, "orquestrador", tmp_path / "ctr")
    extrator = _servico(settings, chaves, "extrator", tmp_path / "ctr")
    certo = a2a_headers(orquestrador, "extrator", QUEM)["Authorization"]
    assert verify_a2a_token(extrator, certo, "extrator")["agent"] == "orquestrador"
    # O extrator tem a própria chave, mas não a do orquestrador: não consegue se passar por ele.
    falso = a2a_headers(extrator, "extrator", QUEM)["Authorization"]
    with pytest.raises(AuthError):
        verify_a2a_token(extrator, falso, "extrator")
    # E o segredo compartilhado deixa de abrir a porta.
    with pytest.raises(AuthError):
        verify_a2a_token(extrator, a2a_headers(settings, "extrator", QUEM)["Authorization"], "extrator")


def test_gerar_chaves_nao_troca_a_que_existe(chaves):
    antes = (chaves / "extrator" / "extrator.pem").read_bytes()
    assert generate_keys(chaves, ["extrator"]) == 0
    assert (chaves / "extrator" / "extrator.pem").read_bytes() == antes
