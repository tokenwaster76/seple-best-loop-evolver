"""SEPLE v5 — Real LLM integration with token tracking from API usage fields."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model: str
    provider: str


class LLMClient:
    """Unified client for OpenRouter, Grok, OpenAI, Anthropic, and Ollama."""

    PROVIDERS = ("openrouter", "grok", "openai", "anthropic", "ollama")

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        timeout: int = 180,
    ) -> None:
        # Smart default: respect explicit env, otherwise prefer local Ollama llama3.1 when no keys
        p = (provider or os.getenv("SEPLE_LLM_PROVIDER"))
        m = (model or os.getenv("SEPLE_MODEL"))
        if not p or not m:
            # Use same auto logic as runner when not explicitly provided
            has_or = bool(os.getenv("OPENROUTER_API_KEY"))
            has_x = bool(os.getenv("XAI_API_KEY"))
            has_oai = bool(os.getenv("OPENAI_API_KEY"))
            has_ant = bool(os.getenv("ANTHROPIC_API_KEY"))
            if p == "ollama" or (not p and not has_or and not has_x and not has_oai and not has_ant):
                p = p or "ollama"
                m = m or "llama3.1"
            elif has_or:
                p = p or "openrouter"
                m = m or "google/gemini-2.5-flash"
            elif has_x:
                p = p or "grok"
                m = m or "grok-3"
            elif has_oai:
                p = p or "openai"
                m = m or "gpt-4o"
            elif has_ant:
                p = p or "anthropic"
                m = m or "claude-3-5-sonnet-20241022"
            else:
                p = p or "ollama"
                m = m or "llama3.1"
        self.provider = p.lower()
        self.model = m
        self.timeout = timeout

        if self.provider not in self.PROVIDERS:
            raise ValueError(
                f"Unknown provider '{self.provider}'. Choose from: {self.PROVIDERS}"
            )
        self._validate_credentials()

    def _default_model(self) -> str:
        defaults = {
            "openrouter": "google/gemini-2.5-flash",
            "grok": "grok-3",
            "openai": "gpt-4o",
            "anthropic": "claude-3-5-sonnet-20241022",
            "ollama": "llama3.1",
        }
        return defaults[self.provider]

    def _validate_credentials(self) -> None:
        required = {
            "openrouter": ("OPENROUTER_API_KEY", "Set OPENROUTER_API_KEY for OpenRouter API."),
            "grok": ("XAI_API_KEY", "Set XAI_API_KEY for Grok/xAI API."),
            "openai": ("OPENAI_API_KEY", "Set OPENAI_API_KEY for OpenAI API."),
            "anthropic": ("ANTHROPIC_API_KEY", "Set ANTHROPIC_API_KEY for Anthropic API."),
        }
        if self.provider in required:
            key_name, msg = required[self.provider]
            if not os.getenv(key_name):
                raise EnvironmentError(msg)

    def chat(self, system: str, user: str, temperature: float = 0.7) -> LLMResponse:
        """Make a real LLM API call and return content + token usage."""
        dispatch = {
            "openrouter": self._chat_openrouter,
            "grok": self._chat_openai_compatible,
            "openai": self._chat_openai_compatible,
            "anthropic": self._chat_anthropic,
            "ollama": self._chat_ollama,
        }
        return dispatch[self.provider](system, user, temperature)

    def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                resp = requests.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(2 ** attempt + 1)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("Request failed without error")

    def _openai_compatible_request(
        self,
        base_url: str,
        api_key: str,
        system: str,
        user: str,
        temperature: float,
        extra_headers: dict[str, str] | None = None,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        resp = self._request_with_retry(
            "POST",
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens))

        if total_tokens == 0 and input_tokens == 0 and output_tokens == 0:
            raise RuntimeError(
                f"No token usage data in {self.provider} response — cannot track tokens."
            )

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=self.model,
            provider=self.provider,
        )

    def _chat_openrouter(
        self, system: str, user: str, temperature: float
    ) -> LLMResponse:
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        extra_headers = {
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/tokenwaster76/seple-best-loop-evolver"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "SEPLE v5"),
        }
        return self._openai_compatible_request(
            base_url,
            os.environ["OPENROUTER_API_KEY"],
            system,
            user,
            temperature,
            extra_headers=extra_headers,
        )

    def _chat_openai_compatible(
        self, system: str, user: str, temperature: float
    ) -> LLMResponse:
        if self.provider == "grok":
            base_url = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
            api_key = os.environ["XAI_API_KEY"]
        else:
            base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            api_key = os.environ["OPENAI_API_KEY"]

        return self._openai_compatible_request(
            base_url, api_key, system, user, temperature
        )

    def _chat_anthropic(
        self, system: str, user: str, temperature: float
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature,
        }
        resp = self._request_with_retry(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        data = resp.json()
        content = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        total_tokens = input_tokens + output_tokens

        if total_tokens == 0:
            raise RuntimeError("No token usage data in Anthropic response.")

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=self.model,
            provider=self.provider,
        )

    def _chat_ollama(
        self, system: str, user: str, temperature: float
    ) -> LLMResponse:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        resp = self._request_with_retry(
            "POST",
            f"{host.rstrip('/')}/api/chat",
            json=payload,
        )
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        input_tokens = int(data.get("prompt_eval_count", 0))
        output_tokens = int(data.get("eval_count", 0))
        total_tokens = input_tokens + output_tokens

        if total_tokens == 0:
            raise RuntimeError(
                "No token usage data in Ollama response (prompt_eval_count/eval_count)."
            )

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=self.model,
            provider=self.provider,
        )


def extract_json(text: str) -> dict[str, Any]:
    """Extract JSON object from LLM response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop opening fence (```json or ```)
        lines = lines[1:]
        # Drop closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find first { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")