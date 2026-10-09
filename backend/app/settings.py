from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://banking_app:replace@localhost:5433/banking_core"
    supabase_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    frontend_origin: str = "http://localhost:5173"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
