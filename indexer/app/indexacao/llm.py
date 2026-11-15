"""Cliente fino sobre a OpenAI para as duas únicas chamadas de LLM da indexação
(metadados e transcrição de gráficos). Sempre com saída estruturada validada por schema.
"""
import base64
from pathlib import Path
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._model = settings.openai_llm_model
        self._client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=3, timeout=120)
        self.input_tokens = 0
        self.output_tokens = 0

    async def parse(self, *, system: str, user: str, schema: type[T], image: Path | None = None) -> T:
        """Uma chamada com saída no formato de `schema`. O conteúdo do documento vai sempre
        na mensagem de usuário, nunca na de sistema."""
        content: list[dict] | str = user
        if image is not None:
            encoded = base64.b64encode(image.read_bytes()).decode()
            content = [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ]
        response = await self._client.chat.completions.parse(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            response_format=schema,
        )
        if response.usage is not None:
            self.input_tokens += response.usage.prompt_tokens
            self.output_tokens += response.usage.completion_tokens
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError("O modelo não devolveu uma resposta no formato esperado.")
        return parsed
