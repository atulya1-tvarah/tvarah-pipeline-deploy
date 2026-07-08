from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("resume_intelligence.llm")

_LAST_LLM_ERROR = ""
_LAST_LLM_PROVIDER_USED = ""
_LLM_TELEMETRY: list[dict[str, Any]] = []


MODEL_CONTEXT_WINDOWS = {
    "mistral-medium-latest": 32768,
    "google/gemma-3-27b-it:free": 8192,
}

MODEL_PRICING_USD_PER_1K = {
    "mistral-medium-latest": {"prompt": 0.00275, "completion": 0.0081},
    "google/gemma-3-27b-it:free": {"prompt": 0.0, "completion": 0.0},
}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _request_with_retries(
    label: str,
    fn,
    retries: int,
):
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            logger.warning("%s attempt %s failed: %s", label, attempt + 1, exc)
    if last_exc:
        raise last_exc
    return None


def _set_last_llm_error(message: str) -> None:
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = message


def _set_last_llm_provider_used(provider: str) -> None:
    global _LAST_LLM_PROVIDER_USED
    _LAST_LLM_PROVIDER_USED = provider


def reset_llm_telemetry() -> None:
    global _LLM_TELEMETRY
    _LLM_TELEMETRY = []


def get_llm_telemetry() -> dict[str, Any]:
    requests_log = list(_LLM_TELEMETRY)
    total_prompt = sum(int(item.get("prompt_tokens", 0) or 0) for item in requests_log)
    total_completion = sum(int(item.get("completion_tokens", 0) or 0) for item in requests_log)
    total_tokens = sum(int(item.get("total_tokens", 0) or 0) for item in requests_log)
    total_latency_ms = sum(float(item.get("latency_ms", 0) or 0) for item in requests_log)
    total_cost = round(sum(float(item.get("cost_usd", 0) or 0) for item in requests_log), 6)
    peak_remaining = max([int(item.get("remaining_context_tokens", 0) or 0) for item in requests_log] or [0])
    return {
        "requests": requests_log,
        "request_count": len(requests_log),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "total_latency_ms": round(total_latency_ms, 2),
        "average_latency_ms": round(total_latency_ms / max(len(requests_log), 1), 2) if requests_log else 0.0,
        "estimated_cost_usd": total_cost,
        "remaining_context_tokens_max": peak_remaining,
    }


def get_last_llm_error() -> str:
    return _LAST_LLM_ERROR


def get_last_llm_provider_used() -> str:
    return _LAST_LLM_PROVIDER_USED or llm_provider()


def _estimate_text_tokens(text: str) -> int:
    return max(1, int(round(len(text or "") / 4)))


def _estimate_message_tokens(messages: list[dict[str, str]]) -> int:
    total = 0
    for message in messages or []:
        total += _estimate_text_tokens(str(message.get("content", ""))) + 8
    return total


def _context_window_for_model(model_name: str) -> int:
    if model_name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_name]
    if "gemma" in model_name.lower():
        return 8192
    if "mistral" in model_name.lower():
        return 32768
    return 8192


def _pricing_for_model(model_name: str) -> dict[str, float]:
    if model_name in MODEL_PRICING_USD_PER_1K:
        return MODEL_PRICING_USD_PER_1K[model_name]
    if "free" in model_name.lower():
        return {"prompt": 0.0, "completion": 0.0}
    return {"prompt": 0.0, "completion": 0.0}


def _usage_from_response(provider: str, data: dict[str, Any]) -> dict[str, int]:
    if provider in {"mistral", "openrouter"}:
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
    if provider == "ollama":
        prompt_tokens = int(data.get("prompt_eval_count", 0) or 0)
        completion_tokens = int(data.get("eval_count", 0) or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _record_llm_telemetry(provider: str, model_name: str, mode: str, messages: list[dict[str, str]], max_tokens: int, usage: dict[str, int] | None, latency_ms: float, success: bool, error: str = "") -> None:
    usage = usage or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", 0) or 0)
    estimated = False
    if prompt_tokens <= 0 and messages:
        prompt_tokens = _estimate_message_tokens(messages)
        estimated = True
    if completion_tokens <= 0 and success:
        completion_tokens = max_tokens
        estimated = True
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    context_window = _context_window_for_model(model_name)
    remaining = max(0, context_window - total_tokens)
    pricing = _pricing_for_model(model_name)
    cost_usd = round((prompt_tokens / 1000.0) * pricing["prompt"] + (completion_tokens / 1000.0) * pricing["completion"], 6)
    _LLM_TELEMETRY.append(
        {
            "provider": provider,
            "model": model_name,
            "mode": mode,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "remaining_context_tokens": remaining,
            "context_window_tokens": context_window,
            "latency_ms": round(latency_ms, 2),
            "cost_usd": cost_usd,
            "estimated_usage": estimated,
            "success": success,
            "error": error,
        }
    )


def llm_provider() -> str:
    if os.getenv("LLM_PROVIDER", "").strip():
        return os.getenv("LLM_PROVIDER", "").strip().lower()
    if os.getenv("MISTRAL_API_KEY", "").strip():
        return "mistral"
    if os.getenv("OLLAMA_MODEL", "").strip():
        return "ollama"
    return "openrouter"


def primary_model(default: str) -> str:
    if llm_provider() == "mistral":
        return os.getenv("MISTRAL_MODEL", os.getenv("PRIMARY_MODEL", default)).strip()
    if llm_provider() == "ollama":
        return os.getenv("OLLAMA_MODEL", default).strip()
    return os.getenv("PRIMARY_MODEL", default).strip()


def scoring_model(default: str) -> str:
    if llm_provider() == "mistral":
        return os.getenv("MISTRAL_SCORING_MODEL", os.getenv("MISTRAL_MODEL", os.getenv("SCORING_MODEL", os.getenv("PRIMARY_MODEL", default)))).strip()
    if llm_provider() == "ollama":
        return os.getenv("OLLAMA_SCORING_MODEL", os.getenv("OLLAMA_MODEL", default)).strip()
    return os.getenv("SCORING_MODEL", os.getenv("PRIMARY_MODEL", default)).strip()


def analysis_model(default: str) -> str:
    if llm_provider() == "mistral":
        return os.getenv("MISTRAL_ANALYSIS_MODEL", os.getenv("MISTRAL_SCORING_MODEL", os.getenv("MISTRAL_MODEL", os.getenv("ANALYSIS_MODEL", os.getenv("SCORING_MODEL", os.getenv("PRIMARY_MODEL", default)))))).strip()
    if llm_provider() == "ollama":
        return os.getenv("OLLAMA_ANALYSIS_MODEL", os.getenv("OLLAMA_SCORING_MODEL", os.getenv("OLLAMA_MODEL", default))).strip()
    return os.getenv("ANALYSIS_MODEL", os.getenv("SCORING_MODEL", os.getenv("PRIMARY_MODEL", default))).strip()


def summary_model(default: str) -> str:
    if llm_provider() == "mistral":
        return os.getenv("MISTRAL_SUMMARY_MODEL", os.getenv("MISTRAL_MODEL", os.getenv("SUMMARY_MODEL", os.getenv("PRIMARY_MODEL", default)))).strip()
    if llm_provider() == "ollama":
        return os.getenv("OLLAMA_SUMMARY_MODEL", os.getenv("OLLAMA_MODEL", default)).strip()
    return os.getenv("SUMMARY_MODEL", os.getenv("PRIMARY_MODEL", default)).strip()


def fallback_cloud_model(default: str) -> str:
    return os.getenv("OPENROUTER_FALLBACK_MODEL", os.getenv("SCORING_MODEL", os.getenv("PRIMARY_MODEL", default))).strip()


def provider_enabled(feature_flag: str = "") -> bool:
    provider = llm_provider()
    if feature_flag and os.getenv(feature_flag, "true").lower() != "true":
        return False
    if provider == "mistral":
        return bool(os.getenv("MISTRAL_API_KEY", "").strip())
    if provider == "ollama":
        return True
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def _openrouter_available() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def _mistral_available() -> bool:
    return bool(os.getenv("MISTRAL_API_KEY", "").strip())


def _json_content_from_openrouter(data: dict[str, Any]) -> str | None:
    content = None
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = None
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        content = "\n".join(part for part in text_parts if part).strip()
    if not isinstance(content, str) or not content.strip():
        _set_last_llm_error("OpenRouter returned an empty content block.")
        return None
    return content


def _json_content_from_mistral(data: dict[str, Any]) -> str | None:
    content = None
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = None
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
                elif "text" in item:
                    text_parts.append(str(item.get("text", "")))
        content = "\n".join(part for part in text_parts if part).strip()
    if not isinstance(content, str) or not content.strip():
        _set_last_llm_error("Mistral returned an empty content block.")
        return None
    return content


def _extract_json_object(content: str) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    text = content.strip()
    decoder = json.JSONDecoder()

    candidates = [text]

    fenced = re.findall(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())

    for start_char in ("{", "["):
        start = text.find(start_char)
        if start != -1:
            snippet = text[start:].strip()
            candidates.append(snippet)

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed, _ = decoder.raw_decode(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    # Last-resort balanced object extraction for models that add prose around JSON.
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        for idx in range(brace_start, len(text)):
            char = text[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = text[brace_start:idx + 1]
                    try:
                        parsed = json.loads(snippet)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        break
    return None


def _repair_truncated_json(content: str) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text or "{" not in text:
        return None
    start = text.find("{")
    snippet = text[start:]
    in_string = False
    escape = False
    brace_depth = 0
    bracket_depth = 0
    repaired_chars: list[str] = []
    for char in snippet:
        repaired_chars.append(char)
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == "\"":
                in_string = False
            continue
        if char == "\"":
            in_string = True
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
    if in_string:
        repaired_chars.append("\"")
    repaired_chars.extend("]" * bracket_depth)
    repaired_chars.extend("}" * brace_depth)
    repaired = "".join(repaired_chars).strip()
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _mistral_request(model_name: str, messages: list[dict[str, str]], max_tokens: int, json_mode: bool, schema: dict[str, Any] | None = None) -> str | None:
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        _set_last_llm_error("Mistral API key is missing.")
        return None
    base_url = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1").strip().rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout_sec = _int_env("MISTRAL_TIMEOUT_SEC", 180)
    retries = _int_env("MISTRAL_RETRIES", 1)
    body: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": float(os.getenv("TEMPERATURE", "0")),
        "max_tokens": max_tokens,
        "random_seed": 7,
    }
    if json_mode:
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "resume_intelligence_schema",
                    "schema": schema,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
    started = time.perf_counter()
    try:
        logger.info("LLM request start provider=mistral model=%s mode=%s max_tokens=%s", model_name, "json" if json_mode else "text", max_tokens)
        def _send():
            with requests.Session() as session:
                session.trust_env = False
                return session.post(f"{base_url}/chat/completions", headers=headers, json=body, timeout=timeout_sec)

        response = _request_with_retries("mistral_request", _send, retries)
        response.raise_for_status()
        response_json = response.json()
        _set_last_llm_provider_used("mistral")
        _set_last_llm_error("")
        _record_llm_telemetry("mistral", model_name, "json" if json_mode else "text", messages, max_tokens, _usage_from_response("mistral", response_json), (time.perf_counter() - started) * 1000, True)
        logger.info("LLM request success provider=mistral model=%s status=%s", model_name, response.status_code)
        return _json_content_from_mistral(response_json)
    except Exception as exc:
        response_text = ""
        if hasattr(exc, "response") and getattr(exc, "response", None) is not None:
            try:
                response_text = (exc.response.text or "").strip()
            except Exception:
                response_text = ""
        detail = f" | Response: {response_text[:500]}" if response_text else ""
        message = f"Mistral request failed: {exc}{detail}"
        _set_last_llm_error(message)
        _record_llm_telemetry("mistral", model_name, "json" if json_mode else "text", messages, max_tokens, None, (time.perf_counter() - started) * 1000, False, message)
        logger.error("LLM request failed provider=mistral model=%s error=%s", model_name, message)
        return None


def _openrouter_request(model_name: str, messages: list[dict[str, str]], max_tokens: int, json_mode: bool, schema: dict[str, Any] | None = None) -> str | None:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        _set_last_llm_error("OpenRouter API key is missing.")
        return None
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout_sec = _int_env("OPENROUTER_TIMEOUT_SEC", 120)
    retries = _int_env("OPENROUTER_RETRIES", 1)
    body: dict[str, Any] = {
        "model": model_name,
        "temperature": float(os.getenv("TEMPERATURE", "0")),
        "max_tokens": max_tokens,
        "provider": {
            "allow_fallbacks": True,
            "data_collection": "deny",
            "require_parameters": True,
        },
        "messages": messages,
    }
    if json_mode:
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "resume_intelligence_schema",
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
    started = time.perf_counter()
    try:
        logger.info("LLM request start provider=openrouter model=%s mode=%s max_tokens=%s", model_name, "json" if json_mode else "text", max_tokens)
        def _send():
            with requests.Session() as session:
                session.trust_env = False
                return session.post(f"{base_url}/chat/completions", headers=headers, json=body, timeout=timeout_sec)

        response = _request_with_retries("openrouter_request", _send, retries)
        response.raise_for_status()
        response_json = response.json()
        _set_last_llm_provider_used("openrouter")
        _set_last_llm_error("")
        _record_llm_telemetry("openrouter", model_name, "json" if json_mode else "text", messages, max_tokens, _usage_from_response("openrouter", response_json), (time.perf_counter() - started) * 1000, True)
        logger.info("LLM request success provider=openrouter model=%s status=%s", model_name, response.status_code)
        return _json_content_from_openrouter(response_json)
    except Exception as exc:
        response_text = ""
        if hasattr(exc, "response") and getattr(exc, "response", None) is not None:
            try:
                response_text = (exc.response.text or "").strip()
            except Exception:
                response_text = ""
        detail = f" | Response: {response_text[:500]}" if response_text else ""
        message = f"OpenRouter request failed: {exc}{detail}"
        _set_last_llm_error(message)
        _record_llm_telemetry("openrouter", model_name, "json" if json_mode else "text", messages, max_tokens, None, (time.perf_counter() - started) * 1000, False, message)
        logger.error("LLM request failed provider=openrouter model=%s error=%s", model_name, message)
        return None


def _ollama_request(model_name: str, messages: list[dict[str, str]], max_tokens: int, json_mode: bool, schema: dict[str, Any] | None = None) -> str | None:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    read_timeout = int(os.getenv("OLLAMA_TIMEOUT_SEC", "300"))
    retries = _int_env("OLLAMA_RETRIES", 0)
    body: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": False,
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "20m"),
        "options": {
            "temperature": float(os.getenv("TEMPERATURE", "0")),
            "num_predict": max_tokens,
        },
    }
    if os.getenv("OLLAMA_NUM_CTX", "").strip():
        body["options"]["num_ctx"] = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    if json_mode:
        body["format"] = schema if schema else "json"
    started = time.perf_counter()
    try:
        logger.info("LLM request start provider=ollama model=%s mode=%s max_tokens=%s", model_name, "json" if json_mode else "text", max_tokens)
        response = _request_with_retries(
            "ollama_request",
            lambda: requests.post(f"{base_url}/api/chat", json=body, timeout=(20, read_timeout)),
            retries,
        )
        response.raise_for_status()
        data = response.json()
        content = ((data.get("message") or {}).get("content") or "").strip()
        if not content:
            _set_last_llm_error("Ollama returned an empty message content.")
            _record_llm_telemetry("ollama", model_name, "json" if json_mode else "text", messages, max_tokens, _usage_from_response("ollama", data), (time.perf_counter() - started) * 1000, False, "Ollama returned an empty message content.")
            logger.error("LLM request failed provider=ollama model=%s error=%s", model_name, "Ollama returned an empty message content.")
            return None
        _set_last_llm_provider_used("ollama")
        _set_last_llm_error("")
        _record_llm_telemetry("ollama", model_name, "json" if json_mode else "text", messages, max_tokens, _usage_from_response("ollama", data), (time.perf_counter() - started) * 1000, True)
        logger.info("LLM request success provider=ollama model=%s", model_name)
        return content or None
    except Exception as exc:
        message = f"Ollama request failed: {exc}"
        _set_last_llm_error(message)
        _record_llm_telemetry("ollama", model_name, "json" if json_mode else "text", messages, max_tokens, None, (time.perf_counter() - started) * 1000, False, message)
        logger.error("LLM request failed provider=ollama model=%s error=%s", model_name, message)
        return None


def call_llm_text(model_name: str, system_prompt: str, user_prompt: str, max_tokens: int = 900) -> str | None:
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    if llm_provider() == "mistral":
        content = _mistral_request(model_name, messages, max_tokens=max_tokens, json_mode=False)
        if content:
            return content
        mistral_error = get_last_llm_error()
        local_model = os.getenv("OLLAMA_SUMMARY_MODEL", "").strip()
        if local_model:
            content = _ollama_request(local_model, messages, max_tokens=max_tokens, json_mode=False)
            if content:
                return content
            ollama_error = get_last_llm_error()
        else:
            ollama_error = ""
        if _openrouter_available():
            content = _openrouter_request(fallback_cloud_model(model_name), messages, max_tokens=max_tokens, json_mode=False)
            if content:
                return content
            openrouter_error = get_last_llm_error()
            _set_last_llm_error(f"Mistral failed, Ollama fallback failed, and OpenRouter fallback failed. Mistral: {mistral_error} | Ollama: {ollama_error} | OpenRouter: {openrouter_error}")
        return None
    if llm_provider() == "ollama":
        content = _ollama_request(model_name, messages, max_tokens=max_tokens, json_mode=False)
        if content:
            return content
        ollama_error = get_last_llm_error()
        if _openrouter_available():
            content = _openrouter_request(os.getenv("SUMMARY_MODEL", os.getenv("PRIMARY_MODEL", model_name)).strip(), messages, max_tokens=max_tokens, json_mode=False)
            if content:
                return content
            openrouter_error = get_last_llm_error()
            _set_last_llm_error(f"Ollama failed, and OpenRouter fallback failed. Ollama: {ollama_error} | OpenRouter: {openrouter_error}")
        return None
    return _openrouter_request(model_name, messages, max_tokens=max_tokens, json_mode=False)


def call_llm_json(model_name: str, messages: list[dict[str, str]], max_tokens: int = 1200, schema: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if llm_provider() == "mistral":
        content = _mistral_request(model_name, messages, max_tokens=max_tokens, json_mode=True, schema=schema)
        if not content:
            mistral_error = get_last_llm_error()
            local_model = os.getenv("OLLAMA_SCORING_MODEL", os.getenv("OLLAMA_MODEL", "")).strip()
            ollama_error = ""
            if local_model:
                content = _ollama_request(local_model, messages, max_tokens=max_tokens, json_mode=True, schema=schema)
                if content:
                    parsed = _extract_json_object(content)
                    if parsed is not None:
                        _set_last_llm_provider_used("ollama")
                        _set_last_llm_error("")
                        logger.info("LLM JSON recovery success provider=ollama model=%s", local_model)
                        return parsed
                ollama_error = get_last_llm_error()
            if not content and _openrouter_available():
                fallback_model = fallback_cloud_model(model_name)
                content = _openrouter_request(fallback_model, messages, max_tokens=max_tokens, json_mode=True, schema=schema)
                if not content:
                    openrouter_error = get_last_llm_error()
                    _set_last_llm_error(f"Mistral failed, Ollama fallback failed, and OpenRouter fallback failed. Mistral: {mistral_error} | Ollama: {ollama_error} | OpenRouter: {openrouter_error}")
    elif llm_provider() == "ollama":
        content = _ollama_request(model_name, messages, max_tokens=max_tokens, json_mode=True, schema=schema)
        if not content and _openrouter_available():
            ollama_error = get_last_llm_error()
            fallback_model = fallback_cloud_model(model_name)
            content = _openrouter_request(fallback_model, messages, max_tokens=max_tokens, json_mode=True, schema=schema)
            if not content:
                openrouter_error = get_last_llm_error()
                _set_last_llm_error(f"Ollama failed, and OpenRouter fallback failed. Ollama: {ollama_error} | OpenRouter: {openrouter_error}")
    else:
        content = _openrouter_request(model_name, messages, max_tokens=max_tokens, json_mode=True, schema=schema)
    if not content:
        return None
    try:
        parsed = json.loads(content)
        _set_last_llm_error("")
        logger.info("LLM JSON parse success provider=%s model=%s", get_last_llm_provider_used(), model_name)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        recovered = _extract_json_object(content)
        if recovered is not None:
            _set_last_llm_error("")
            logger.info("LLM JSON recovery success provider=%s model=%s", get_last_llm_provider_used(), model_name)
            return recovered
        repaired = _repair_truncated_json(content)
        if repaired is not None:
            _set_last_llm_error("")
            logger.info("LLM JSON truncation repair success provider=%s model=%s", get_last_llm_provider_used(), model_name)
            return repaired
        preview = content[:400].replace("\n", "\\n")
        message = f"LLM returned invalid JSON: {exc}"
        _set_last_llm_error(message)
        logger.error("LLM JSON parse failed provider=%s model=%s error=%s preview=%s", get_last_llm_provider_used(), model_name, message, preview)
        return None
