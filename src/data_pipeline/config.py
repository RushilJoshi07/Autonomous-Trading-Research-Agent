from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    database_url_test: str
    log_level: str = "INFO"

    # Read here only so Settings() doesn't choke on .env containing them
    # (BaseSettings forbids unmapped env vars by default) -- the langsmith
    # SDK itself reads these same names directly from os.environ, not
    # through this object; see llm_client's @traceable usage.
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None


settings = Settings()
