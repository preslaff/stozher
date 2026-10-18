from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).parents[4] / "infra" / ".env"  # git-ignored; see infra/.env.example


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else None, extra="ignore"
    )

    stozher_api_url: str = "http://127.0.0.1:8000"
    stozher_system_key: str = (
        ""  # only used for the heartbeat; questions run with the asker's own key
    )
    stozher_assistant_host: str = "127.0.0.1"  # loopback: the API forwards questions here
    stozher_assistant_port: int = 8100
    # auto = the cloud model when it is switched on and reachable, else the local one; local / cloud force one of them.
    stozher_assistant_engine: str = "auto"
    # Local engine: llama.cpp `llama-server --jinja` (default) or Ollama, with an open-weight instruct model that can call tools.
    stozher_assistant_local_backend: str = "llamacpp"  # llamacpp | ollama
    stozher_llamacpp_url: str = "http://127.0.0.1:8080"
    stozher_ollama_url: str = "http://127.0.0.1:11434"
    # Empty: llama-server uses the model it loaded; Ollama falls back to qwen2.5:7b-instruct.
    stozher_assistant_local_model: str = ""
    # Cloud engine: used only when BOTH are set. Situation summaries leave this machine then, so the default is off.
    stozher_assistant_cloud: str = "off"
    anthropic_api_key: str = ""
    stozher_assistant_cloud_model: str = "claude-sonnet-5-5"
    stozher_assistant_timeout_s: float = 90.0
    stozher_assistant_max_steps: int = 6

    @property
    def cloud_enabled(self) -> bool:
        return self.stozher_assistant_cloud.lower() == "on" and bool(self.anthropic_api_key)
