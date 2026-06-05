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
    FORCE_RESET: bool = True

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