from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = "ARGUS API"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "sqlite:///./argus.db"
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    DEFAULT_MUNICIPIO: str = "Macae"
    TCE_BASE_URL: str = "https://dados.tcerj.tc.br/api/v1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.BACKEND_CORS_ORIGINS.split(",") if item.strip()]

@lru_cache
def get_settings() -> Settings:
    return Settings()
