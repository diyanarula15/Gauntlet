from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
ROOT = Path(__file__).resolve().parents[2]
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / '.env'), extra='ignore')
    database_path: str = str(ROOT / 'data/faultline.db')
    bifrost_base_url: str = 'http://127.0.0.1:8080'
    bifrost_api_key: str = ''
    bifrost_model: str = 'openai/gpt-4o-mini'
    tavily_api_key: str = ''
    harness_base_url: str = 'http://127.0.0.1:8017'
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    max_model_calls: int = 12
    max_search_calls: int = 12
    max_concurrent_runs: int = 3
    max_experiment_runs: int = 600
    model_timeout_seconds: float = 24
    tool_timeout_seconds: float = 10
    bifrost_logs_timeout_seconds: float = 3
    bifrost_admin_api_key: str = ''
    tavily_credit_price_usd: float | None = None
    docker_harness_base_url: str = 'http://host.docker.internal:8017'
    default_max_cost_usd: float | None = None
    max_output_tokens: int = 1024
settings = Settings()
