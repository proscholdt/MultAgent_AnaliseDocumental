"""Sala dos agentes: o servidor que junta, para a página, os passos da execução (orquestrador),
a auditoria do servidor MCP (cada chamada de tool, na hora) e os logs do Loki.

Uma sessão por vez: o login fica no servidor, e quem abrir a página vê a mesma sessão. É uma
ferramenta de acompanhamento para uso local, não um serviço multiusuário.
"""
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

INDEXADOR = os.getenv("INDEXER_URL", "http://api:8000")
AGENTES = os.getenv("AGENTS_URL", "http://orquestrador:8200")
MCP = os.getenv("MCP_URL", "http://mcp-server:8100")
LOKI = os.getenv("LOKI_URL", "http://loki:3100")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
AQUI = Path(__file__).parent

app = FastAPI(title="Sala dos agentes")
cliente = httpx.AsyncClient(timeout=30)
sessao: dict[str, Any] = {"token": None, "usuario": None, "vence": 0.0}


class Entrada(BaseModel):
    usuario: str
    senha: str


def _token() -> str:
    if not sessao["token"] or sessao["vence"] - time.time() < 16 * 60:
        raise HTTPException(401, "Entre de novo.")
    return sessao["token"]


@app.get("/")
async def pagina() -> FileResponse:
    return FileResponse(AQUI / "index.html")


@app.post("/api/entrar")
async def entrar(dados: Entrada) -> dict[str, Any]:
    resposta = await cliente.post(f"{INDEXADOR}/auth/login", data={"username": dados.usuario, "password": dados.senha})
    if resposta.status_code != 200:
        raise HTTPException(401, "Usuário ou senha inválidos.")
    corpo = resposta.json()
    sessao.update(token=corpo["access_token"], usuario=dados.usuario, vence=time.time() + corpo["expires_in"])
    return {"usuario": dados.usuario}


@app.get("/api/sessao")
async def ver_sessao() -> dict[str, Any]:
    _token()
    return {"usuario": sessao["usuario"]}


@app.get("/api/execucoes")
async def execucoes() -> list[dict[str, Any]]:
    resposta = await cliente.get(f"{AGENTES}/api/execucoes", headers={"Authorization": f"Bearer {_token()}"})
    resposta.raise_for_status()
    return resposta.json()[:20]


@app.get("/api/documentos")
async def documentos() -> dict[str, str]:
    resposta = await cliente.get(f"{INDEXADOR}/api/documents", headers={"Authorization": f"Bearer {_token()}"})
    resposta.raise_for_status()
    return {d["doc_id"]: d.get("company") or d["file_name"] for d in resposta.json()}


@app.get("/api/estado")
async def estado(execucao: str, desde: int = 0) -> dict[str, Any]:
    cabecalho = {"Authorization": f"Bearer {_token()}"}
    resposta = await cliente.get(f"{AGENTES}/api/execucoes/{execucao}", headers=cabecalho)
    if resposta.status_code == 404:
        raise HTTPException(404, "Execução não encontrada.")
    resposta.raise_for_status()
    run = resposta.json()
    passos = run.pop("passos", []) or []
    resultado = run.pop("resultado", None) or {}
    run["tem_relatorio"] = bool(resultado.get("relatorio"))
    run["respostas"] = len(resultado.get("respostas") or [])
    auditoria: list[dict[str, Any]] = []
    if ADMIN_KEY:
        r = await cliente.get(f"{MCP}/auditoria", params={"run_id": execucao, "limite": 1000},
                              headers={"X-Admin-Key": ADMIN_KEY})
        if r.status_code == 200:
            auditoria = [linha for linha in r.json() if linha["tipo"] == "chamada"]
            auditoria.reverse()  # da mais antiga para a mais nova
    return {"execucao": run, "passos": passos[desde:], "total_passos": len(passos), "auditoria": auditoria}


@app.get("/api/loki")
async def loki(consulta: str = '{projeto=~"agents|mcp-server"} |~ "passo=|tool="', minutos: int = 30, limite: int = 300) -> list[dict]:
    fim = time.time_ns()
    r = await cliente.get(f"{LOKI}/loki/api/v1/query_range", params={
        "query": consulta, "start": fim - minutos * 60 * 10**9, "end": fim, "limit": limite, "direction": "backward"})
    if r.status_code != 200:
        raise HTTPException(502, r.text[:300])
    linhas = []
    for fluxo in r.json()["data"]["result"]:
        for ts, linha in fluxo["values"]:
            linhas.append({"ts": int(ts), "servico": fluxo["stream"].get("servico", ""), "linha": linha})
    linhas.sort(key=lambda item: item["ts"], reverse=True)
    return linhas[:limite]
