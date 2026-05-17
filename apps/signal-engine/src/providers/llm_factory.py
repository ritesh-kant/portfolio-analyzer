"""AI provider factory — Python mirror of apps/signals-service/src/lib/llm/llmProvider.ts.

Provider selection: AI_PROVIDER env var → anthropic | openai | gemini | kimi | deepseek | ollama
"""

from langchain_anthropic import ChatAnthropic
from langchain_deepseek import ChatDeepSeek
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from src.config import Settings

_VALID_PROVIDERS = {"anthropic", "openai", "gemini", "kimi", "deepseek", "ollama"}


def get_llm(provider: str | None = None, settings: Settings | None = None) -> object:
    """Return a LangChain BaseChatModel for the given provider name.

    Falls back to AI_PROVIDER env var when provider is None.
    """
    s = settings or Settings()
    name = (provider or s.ai_provider).lower()

    if name not in _VALID_PROVIDERS:
        raise ValueError(
            f"Unknown AI_PROVIDER: '{name}'. Valid values: {', '.join(sorted(_VALID_PROVIDERS))}"
        )

    if name == "anthropic":
        return ChatAnthropic(  # type: ignore[call-arg]
            model=s.anthropic_model,
            api_key=s.anthropic_api_key,  # type: ignore[arg-type]
            max_tokens=2048,
        )

    if name == "openai":
        return ChatOpenAI(
            model=s.openai_model,
            api_key=s.openai_api_key,  # type: ignore[arg-type]
            model_kwargs={"max_tokens": 2048},
        )

    if name == "gemini":
        return ChatGoogleGenerativeAI(
            model=s.gemini_model,
            google_api_key=s.gemini_api_key,
        )

    if name == "kimi":
        # Kimi runs on NVIDIA-hosted inference (OpenAI-compatible API)
        return ChatOpenAI(
            model=s.kimi_model,
            api_key=s.nvidia_api_key,  # type: ignore[arg-type]
            base_url=s.nvidia_base_url,
            model_kwargs={"max_tokens": 2048},
        )

    if name == "deepseek":
        return ChatDeepSeek(
            model=s.deepseek_model,
            api_key=s.deepseek_api_key,  # type: ignore[arg-type]
            api_base=s.deepseek_base_url,
            max_tokens=2048,
        )

    # ollama — local, no API key
    return ChatOllama(
        model=s.ollama_model,
        base_url=s.ollama_base_url,
    )
