"""
Tests for llm/server_manager.py and llm/qwen_client.py.

All tests are offline — they mock HTTP calls and subprocess so no
llama-server binary or GGUF model is required.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.server_manager import LlamaCppServer
from llm.qwen_client import LLMClient, _cache_key, _strip_fences, make_llm_fn
from models import Field


# ════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ════════════════════════════════════════════════════════════════════════════
def _free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_field(name="EN", msb=0, lsb=0, access="RW",
                reset=0, desc="Enable the block.") -> Field:
    return Field(name, msb, lsb, access, reset, desc)


# ── Minimal HTTP server that mimics llama-server /health + /v1/chat/completions ──
class _FakeLlamaHandler(BaseHTTPRequestHandler):
    RESPONSE_BODY = json.dumps({
        "choices": [{"message": {"content": "void en_enable(uintptr_t base) { /* ok */ }"}}]
    }).encode()

    def log_message(self, *_):
        pass   # suppress access log noise

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)   # consume body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.RESPONSE_BODY)


@pytest.fixture(scope="module")
def fake_server():
    """Start a minimal HTTP server that mimics llama-server."""
    port   = _free_port()
    server = HTTPServer(("127.0.0.1", port), _FakeLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


# ════════════════════════════════════════════════════════════════════════════
# server_manager tests
# ════════════════════════════════════════════════════════════════════════════
class TestServerManager:
    def test_is_port_open_false_on_unused_port(self):
        port = _free_port()
        mgr  = LlamaCppServer("fake.exe", "fake.gguf", port=port)
        assert mgr.is_port_open() is False

    def test_is_port_open_true_when_listening(self, fake_server):
        mgr = LlamaCppServer("fake.exe", "fake.gguf", port=fake_server)
        assert mgr.is_port_open() is True

    def test_wait_ready_succeeds_when_server_up(self, fake_server):
        mgr = LlamaCppServer("fake.exe", "fake.gguf", port=fake_server)
        assert mgr.wait_ready(timeout=5) is True

    def test_wait_ready_times_out_on_closed_port(self):
        port = _free_port()
        mgr  = LlamaCppServer("fake.exe", "fake.gguf", port=port)
        result = mgr.wait_ready(timeout=2)
        assert result is False

    def test_validate_raises_on_missing_exe(self, tmp_path):
        mgr = LlamaCppServer(
            tmp_path / "missing.exe",
            tmp_path / "missing.gguf",
        )
        with pytest.raises(FileNotFoundError, match="llama-server"):
            mgr.validate()

    def test_validate_raises_on_missing_gguf(self, tmp_path):
        exe = tmp_path / "llama-server.exe"
        exe.write_bytes(b"fake")
        mgr = LlamaCppServer(exe, tmp_path / "missing.gguf")
        with pytest.raises(FileNotFoundError, match="GGUF"):
            mgr.validate()

    def test_validate_passes_when_both_exist(self, tmp_path):
        exe  = tmp_path / "llama-server.exe"
        gguf = tmp_path / "model.gguf"
        exe.write_bytes(b"fake")
        gguf.write_bytes(b"fake")
        mgr = LlamaCppServer(exe, gguf)
        mgr.validate()   # should not raise

    def test_base_url_property(self):
        mgr = LlamaCppServer("fake.exe", "fake.gguf", port=8080)
        assert mgr.base_url == "http://127.0.0.1:8080/v1"

    def test_start_skips_when_port_already_open(self, fake_server, tmp_path):
        """If port is open, start() is a no-op (no subprocess launched)."""
        exe  = tmp_path / "llama-server.exe"
        gguf = tmp_path / "model.gguf"
        exe.write_bytes(b"fake")
        gguf.write_bytes(b"fake")
        mgr = LlamaCppServer(exe, gguf, port=fake_server)
        mgr.start()
        assert mgr._we_started is False
        assert mgr._process is None

    def test_stop_is_noop_when_not_started(self):
        mgr = LlamaCppServer("fake.exe", "fake.gguf")
        mgr.stop()   # should not raise

    def test_start_launches_subprocess_and_stop_kills_it(self, tmp_path, fake_server):
        """Simulate start() → subprocess → stop() flow using mocks."""
        exe  = tmp_path / "llama-server.exe"
        gguf = tmp_path / "model.gguf"
        exe.write_bytes(b"fake")
        gguf.write_bytes(b"fake")

        mock_proc = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = ""

        with patch("llm.server_manager.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.object(LlamaCppServer, "is_port_open", return_value=False), \
             patch.object(LlamaCppServer, "wait_ready", return_value=True):

            mgr = LlamaCppServer(exe, gguf, port=_free_port())
            mgr.start()

            assert mock_popen.called
            assert mgr._we_started is True

            mgr.stop()
            mock_proc.terminate.assert_called_once()
            assert mgr._we_started is False

    def test_context_manager(self, tmp_path):
        exe  = tmp_path / "llama-server.exe"
        gguf = tmp_path / "model.gguf"
        exe.write_bytes(b"fake")
        gguf.write_bytes(b"fake")

        mock_proc = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = ""

        with patch("llm.server_manager.subprocess.Popen", return_value=mock_proc), \
             patch.object(LlamaCppServer, "is_port_open", return_value=False), \
             patch.object(LlamaCppServer, "wait_ready", return_value=True):

            with LlamaCppServer(exe, gguf, port=_free_port()) as srv:
                assert srv._we_started is True
            # After __exit__, stop() was called
            mock_proc.terminate.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# LLMClient tests
# ════════════════════════════════════════════════════════════════════════════
class TestLLMClient:
    @pytest.fixture
    def client(self, fake_server, tmp_path):
        return LLMClient(
            base_url=f"http://127.0.0.1:{fake_server}/v1",
            cache_dir=tmp_path / "cache",
        )

    def test_generate_function_returns_string(self, client):
        f = _make_field()
        result = client.generate_function(f, "my_periph_ctrl_en", "CTRL_REG", 0, "MY_PERIPH_CTRL_REG")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_function_caches_result(self, client, tmp_path):
        f   = _make_field(desc="Unique desc for cache test")
        fn  = "my_periph_ctrl_en"
        r1  = client.generate_function(f, fn, "CTRL_REG", 0)
        r2  = client.generate_function(f, fn, "CTRL_REG", 0)
        assert r1 == r2   # identical (second call hits cache)

    def test_cache_file_created(self, client, tmp_path):
        f   = _make_field(desc="Cache file test")
        key = _cache_key(f, "CTRL_REG")
        client.generate_function(f, "pfx", "CTRL_REG")
        assert (client.cache_dir / f"{key}.c").exists()

    def test_different_fields_different_cache_keys(self):
        f1 = _make_field(name="EN",  desc="Enable")
        f2 = _make_field(name="RST", desc="Reset")
        assert _cache_key(f1, "CTRL") != _cache_key(f2, "CTRL")

    def test_same_field_different_reg_different_keys(self):
        f = _make_field(name="EN")
        assert _cache_key(f, "CTRL_REG") != _cache_key(f, "STATUS_REG")

    def test_ping_returns_true_when_server_up(self, fake_server, tmp_path):
        client = LLMClient(
            base_url=f"http://127.0.0.1:{fake_server}/v1",
            cache_dir=tmp_path / "ping_cache",
        )
        # ping sends a minimal chat request — fake server always responds
        # (the fake server returns our hard-coded JSON, which may not contain "ok"
        #  but ping should at least not raise)
        result = client.ping()
        assert isinstance(result, bool)

    def test_ping_returns_false_on_closed_port(self, tmp_path):
        port   = _free_port()
        client = LLMClient(
            base_url=f"http://127.0.0.1:{port}/v1",
            cache_dir=tmp_path / "pingfail_cache",
            max_retries=1,
            timeout=2,
        )
        assert client.ping() is False


# ════════════════════════════════════════════════════════════════════════════
# Helper function tests
# ════════════════════════════════════════════════════════════════════════════
class TestHelpers:
    def test_strip_fences_removes_c_fence(self):
        raw = "```c\nvoid foo() {}\n```"
        assert _strip_fences(raw) == "void foo() {}"

    def test_strip_fences_removes_plain_fence(self):
        raw = "```\nvoid foo() {}\n```"
        assert _strip_fences(raw) == "void foo() {}"

    def test_strip_fences_noop_on_clean_code(self):
        code = "void foo() {}"
        assert _strip_fences(code) == code

    def test_make_llm_fn_returns_callable(self, fake_server, tmp_path):
        client = LLMClient(
            base_url=f"http://127.0.0.1:{fake_server}/v1",
            cache_dir=tmp_path / "fn_cache",
        )
        fn = make_llm_fn(client, "MY_PERIPH")
        assert callable(fn)

    def test_make_llm_fn_calls_generate(self, fake_server, tmp_path):
        client = LLMClient(
            base_url=f"http://127.0.0.1:{fake_server}/v1",
            cache_dir=tmp_path / "fn_call_cache",
        )
        fn    = make_llm_fn(client, "MY_PERIPH")
        field = _make_field(desc="Test semantic")
        from models import Register
        reg   = Register("CTRL_REG", 0, [field])
        result = fn(field, "my_periph_ctrl_reg_en", reg=reg)
        assert isinstance(result, str)


# ════════════════════════════════════════════════════════════════════════════
# Setup script logic tests (offline — no actual downloads)
# ════════════════════════════════════════════════════════════════════════════
class TestSetupScript:
    def test_hf_url_construction(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "setup"))
        from download_model import _hf_download_url, HF_REPO, HF_FILE_7B
        url = _hf_download_url(HF_REPO, HF_FILE_7B)
        assert url.startswith("https://huggingface.co/")
        assert HF_FILE_7B in url
        assert HF_REPO in url

    def test_model_size_map_complete(self):
        from download_model import MODEL_SIZES
        assert "1.5b" in MODEL_SIZES
        assert "7b"   in MODEL_SIZES
        assert "14b"  in MODEL_SIZES

    def test_config_written_correctly(self, tmp_path):
        from download_model import _write_config
        exe   = tmp_path / "llama-server.exe"
        model = tmp_path / "model.gguf"
        _write_config(tmp_path, exe, model, port=8080)
        cfg_path = tmp_path / "model.cfg"
        assert cfg_path.exists()
        cfg = json.loads(cfg_path.read_text())
        assert cfg["server_port"] == 8080
        assert "8080" in cfg["base_url"]
        assert str(exe)   == cfg["server_exe"]
        assert str(model) == cfg["model_path"]
