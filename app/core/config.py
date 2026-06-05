from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "ARGUS API"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "sqlite:///./argus.db"
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    DEFAULT_MUNICIPIO: str = "Macae"
    TCE_BASE_URL: str = "https://dados.tcerj.tc.br/api/v1"
    # Quando True, limpa TODOS os registros de TODAS as tabelas antes de rodar
    # o sync job. Útil após alterações na lógica de filtros, scoring, etc.
    # Deve ser False no dia a dia (comportamento acumulativo normal).
    # NOTA: model_cache NUNCA é afetado por FORCE_RESET.
    FORCE_RESET: bool = False

    # ── Google Maps Geocoding API ──
    GOOGLE_MAPS_API_KEY: str = ""
    # Limite de endereços para geocodificar (0 = sem limite, processa todos)
    # Útil para testar com um número pequeno antes de rodar tudo
    GEOCODE_LIMIT: int = 10
    # Quando True, endereços que a API não conseguir geocodificar recebem
    # coordenadas aleatórias dentro do polígono de Macaé (fallback).
    # Quando False, esses endereços são apenas ignorados (sem coordenadas).
    GEOCODE_FALLBACK_RANDOM: bool = False

    # ── OpenRouter (Pipeline de IA) ──
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL_ID: str = "openai/gpt-oss-120b"
    OPENROUTER_PROVIDER: str = "Groq"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Número máximo de workers paralelos para chamadas ao modelo
    AI_PIPELINE_MAX_WORKERS: int = 4
    # Timeout em segundos para cada chamada ao modelo
    AI_PIPELINE_TIMEOUT: int = 60
    # Limite de descrições para processar (0 = sem limite, processa todas)
    # Útil para testar com um número pequeno antes de rodar tudo
    AI_PIPELINE_LIMIT: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins(self) -> list[str]:
        # Em development, permite qualquer origem para facilitar o desenvolvimento
        # local (evita problemas de CORS com diferentes portas do frontend)
        if self.ENVIRONMENT == "development":
            return ["*"]
        origins = [
            item.strip()
            for item in self.BACKEND_CORS_ORIGINS.split(",")
            if item.strip()
        ]
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()