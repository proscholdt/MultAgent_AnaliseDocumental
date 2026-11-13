import os

# O PydanticAI imprime uma propaganda de observabilidade no primeiro uso; nos serviços, não.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
