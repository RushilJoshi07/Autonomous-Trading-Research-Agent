from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    database_url_test: str
    log_level: str = "INFO"

    # Read here only so Settings() doesn't choke on .env containing them
    # (BaseSettings forbids unmapped env vars by default) -- both SDKs read
    # these same names directly from os.environ on their own (confirmed for
    # langsmith in llm_client's @traceable usage; confirmed for tavily-python
    # by reading tavily/tavily.py's own Client.__init__, which falls back to
    # os.getenv("TAVILY_API_KEY") when no api_key is passed explicitly --
    # Component 3 will rely on that same fallback, not this object).
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None
    tavily_api_key: str | None = None


settings = Settings()
