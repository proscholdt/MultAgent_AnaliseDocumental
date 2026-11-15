"""Quem é o usuário. O login é o do indexador: esta API não tem senha nem usuário próprios.

O token que chega em `Authorization: Bearer` é o JWT do indexador. Quem o confere é o
indexador (`/auth/me`); aqui ele só é repassado, e segue com a execução até o servidor MCP.
"""
import time

import httpx
import jwt
from fastapi import HTTPException, status

from app.config import Settings


class Users:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings, self.transport = settings, transport

    async def identify(self, token: str) -> str:
        try:
            async with httpx.AsyncClient(base_url=self.settings.indexer_url, transport=self.transport,
                                         timeout=15) as http:
                response = await http.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                f"Indexador fora do ar ({type(error).__name__}).") from error
        if response.status_code == 401:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado. Faça login no indexador.")
        if response.status_code != 200:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                f"O indexador respondeu {response.status_code} ao conferir o usuário.")
        return response.json()["user_id"]

    def require_time(self, token: str) -> None:
        """Recusa começar uma execução com o token perto de vencer: as buscas falhariam no meio.
        O `exp` é lido sem conferir a assinatura (isso o indexador já fez)."""
        try:
            expires = jwt.decode(token, options={"verify_signature": False}).get("exp")
        except jwt.PyJWTError:
            return
        minutes = (expires - time.time()) / 60 if expires else None
        if minutes is not None and minutes < self.settings.min_minutos_token:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"O token vence em {max(0, int(minutes))} min, e uma execução precisa de pelo menos "
                f"{self.settings.min_minutos_token} min. Faça login de novo no indexador.")
