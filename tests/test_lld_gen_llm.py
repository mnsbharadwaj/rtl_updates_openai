"""
Tests for lld_gen/llm_client.py.
Covers config parsing, local/cloud routing, and cloud endpoint payload verification.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lld_gen.llm_client import LLMConfig, LLMClient, load_llm_config


def test_load_llm_config_nested():
    """Verify that a nested llm config block is parsed correctly."""
    data = {
        "llm": {
            "backend": "ollama",
            "model": "gpt-oss",
            "location": "cloud",
            "temperature": 0.05
        }
    }
    cfg = load_llm_config(data)
    assert cfg.backend == "ollama"
    assert cfg.model == "gpt-oss"
    assert cfg.location == "cloud"
    assert cfg.temperature == 0.05


def test_load_llm_config_flat_location():
    """Verify flat location keys (llm_location, cloud_or_local) are recognized."""
    data = {
        "llm_location": "cloud",
        "llm": {
            "backend": "ollama",
            "model": "qwen2.5-coder:7b"
        }
    }
    cfg = load_llm_config(data)
    assert cfg.location == "cloud"

    data2 = {
        "cloud_or_local": "cloud",
        "llm": {
            "backend": "ollama"
        }
    }
    cfg2 = load_llm_config(data2)
    assert cfg2.location == "cloud"


@patch("requests.post")
def test_cloud_llm_routing_payload(mock_post):
    """Verify that cloud location routing calls correct url with gpt-oss model and prompt payload."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "static inline void patched_fn() {}"}
    mock_post.return_value = mock_resp

    cfg = LLMConfig(
        backend="ollama",
        model="qwen2.5-coder:7b",
        location="cloud"
    )
    # Instantiate client
    client = LLMClient(cfg)
    assert client.available is True
    assert client._backend_fn == client._call_cloud_ollama

    # Invoke backend function
    res = client._backend_fn(system="system instruction", user="user prompt", max_tokens=100)
    assert res == "static inline void patched_fn() {}"

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://107.99.41.85/ollama/srv1/api/generate"
    payload = kwargs["json"]
    assert payload["model"] == "gpt-oss"
    assert payload["stream"] is False
    assert "System Instruction:\nsystem instruction" in payload["prompt"]
    assert "User Context and Request:\nuser prompt" in payload["prompt"]


@patch("requests.post")
def test_cloud_llm_custom_url_and_model_override(mock_post):
    """Verify that cloud location routing respects custom URL and model name config overrides."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "static inline void overridden_fn() {}"}
    mock_post.return_value = mock_resp

    # Config with explicit custom URL (no API path suffix) and model
    cfg = LLMConfig(
        backend="ollama",
        model="custom-coder-30b",
        url="http://my-cloud-ollama:11434/ollama-endpoint",
        location="cloud"
    )
    client = LLMClient(cfg)
    assert client.available is True
    assert client._backend_fn == client._call_cloud_ollama

    res = client._backend_fn(system="sys", user="user", max_tokens=100)
    assert res == "static inline void overridden_fn() {}"

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    # Suffix '/api/generate' should be automatically appended to custom url without suffix
    assert args[0] == "http://my-cloud-ollama:11434/ollama-endpoint/api/generate"
    payload = kwargs["json"]
    # Model should be overridden with the custom name
    assert payload["model"] == "custom-coder-30b"
    assert payload["stream"] is False
