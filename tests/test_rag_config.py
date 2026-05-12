from textwrap import dedent

import pytest

from config.settings import RagConfig, get_rag_config


def _write_config(path, body: str):
    path.write_text(dedent(body), encoding="utf-8")


def test_get_rag_config_returns_none_when_block_absent(tmp_path, monkeypatch):
    cfg = tmp_path / "instances.yaml"
    _write_config(
        cfg,
        """
        instances:
          - name: lab
            host: https://apstra.local
            username: admin
            password: secret
            ssl_verify: false
        """,
    )
    monkeypatch.setenv("APSTRA_CONFIG_FILE", str(cfg))

    assert get_rag_config() is None


def test_get_rag_config_returns_none_when_disabled(tmp_path, monkeypatch):
    cfg = tmp_path / "instances.yaml"
    _write_config(
        cfg,
        """
        rag:
          enabled: false
          embedding_provider: ollama
          embedding_model: nomic-embed-text
          embedding_url: http://localhost:11434/api/embeddings
        """,
    )
    monkeypatch.setenv("APSTRA_CONFIG_FILE", str(cfg))

    assert get_rag_config() is None


def test_get_rag_config_parses_enabled_block(tmp_path, monkeypatch):
    cfg = tmp_path / "instances.yaml"
    _write_config(
        cfg,
        """
        rag:
          enabled: true
          embedding_provider: ollama
          embedding_model: nomic-embed-text
          embedding_url: http://localhost:11434/api/embeddings
          top_k: 7
        """,
    )
    monkeypatch.setenv("APSTRA_CONFIG_FILE", str(cfg))

    got = get_rag_config()
    assert isinstance(got, RagConfig)
    assert got.enabled is True
    assert got.embedding_provider == "ollama"
    assert got.embedding_model == "nomic-embed-text"
    assert got.embedding_url == "http://localhost:11434/api/embeddings"
    assert got.top_k == 7


def test_get_rag_config_raises_when_required_keys_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "instances.yaml"
    _write_config(
        cfg,
        """
        rag:
          enabled: true
          embedding_provider: ollama
        """,
    )
    monkeypatch.setenv("APSTRA_CONFIG_FILE", str(cfg))

    with pytest.raises(ValueError, match="missing required settings"):
        get_rag_config()


def test_get_rag_config_returns_none_when_env_disabled(monkeypatch):
    monkeypatch.delenv("APSTRA_CONFIG_FILE", raising=False)
    monkeypatch.delenv("APSTRA_RAG_ENABLED", raising=False)
    monkeypatch.delenv("APSTRA_RAG_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("APSTRA_RAG_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("APSTRA_RAG_EMBEDDING_URL", raising=False)

    assert get_rag_config() is None


def test_get_rag_config_loads_from_env_vars(monkeypatch):
    monkeypatch.delenv("APSTRA_CONFIG_FILE", raising=False)
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "true")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_MODEL", "qwen3-embedding")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_URL", "http://localhost:11434/api/embed")
    monkeypatch.setenv("APSTRA_RAG_TOP_K", "10")

    got = get_rag_config()
    assert isinstance(got, RagConfig)
    assert got.enabled is True
    assert got.embedding_provider == "ollama"
    assert got.embedding_model == "qwen3-embedding"
    assert got.embedding_url == "http://localhost:11434/api/embed"
    assert got.top_k == 10


def test_get_rag_config_env_enabled_variations(monkeypatch):
    """Test that APSTRA_RAG_ENABLED accepts multiple truthy values."""
    monkeypatch.delenv("APSTRA_CONFIG_FILE", raising=False)
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_MODEL", "qwen3-embedding")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_URL", "http://localhost:11434/api/embed")

    # Test "1"
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "1")
    assert get_rag_config() is not None

    # Test "yes"
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "yes")
    assert get_rag_config() is not None

    # Test "false" should return None
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "false")
    assert get_rag_config() is None


def test_get_rag_config_env_missing_required_field(monkeypatch):
    """Test that missing env vars raise validation error."""
    monkeypatch.delenv("APSTRA_CONFIG_FILE", raising=False)
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "true")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_PROVIDER", "ollama")
    # Missing embedding_model and embedding_url

    with pytest.raises(ValueError, match="missing required environment variables"):
        get_rag_config()


def test_get_rag_config_env_default_top_k(monkeypatch):
    """Test that APSTRA_RAG_TOP_K defaults to 5 when not set."""
    monkeypatch.delenv("APSTRA_CONFIG_FILE", raising=False)
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "true")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_PROVIDER", "lmstudio")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_URL", "http://localhost:8000/v1/embeddings")
    monkeypatch.delenv("APSTRA_RAG_TOP_K", raising=False)

    got = get_rag_config()
    assert got.top_k == 5


def test_get_rag_config_env_invalid_top_k(monkeypatch):
    """Test that invalid APSTRA_RAG_TOP_K raises error."""
    monkeypatch.delenv("APSTRA_CONFIG_FILE", raising=False)
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "true")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_MODEL", "qwen3-embedding")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_URL", "http://localhost:11434/api/embed")
    monkeypatch.setenv("APSTRA_RAG_TOP_K", "not_a_number")

    with pytest.raises(ValueError, match="APSTRA_RAG_TOP_K must be an integer"):
        get_rag_config()


def test_get_rag_config_yaml_takes_precedence_over_env(tmp_path, monkeypatch):
    """Test that YAML config takes precedence over environment variables."""
    cfg = tmp_path / "instances.yaml"
    _write_config(
        cfg,
        """
        rag:
          enabled: true
          embedding_provider: ollama
          embedding_model: qwen3-embedding
          embedding_url: http://localhost:11434/api/embed
          top_k: 5
        """,
    )
    monkeypatch.setenv("APSTRA_CONFIG_FILE", str(cfg))
    # Set contradictory env vars
    monkeypatch.setenv("APSTRA_RAG_ENABLED", "true")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_PROVIDER", "lmstudio")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_MODEL", "different-model")
    monkeypatch.setenv("APSTRA_RAG_EMBEDDING_URL", "http://different:8000/v1/embeddings")
    monkeypatch.setenv("APSTRA_RAG_TOP_K", "99")

    # Should get YAML values, not env values
    got = get_rag_config()
    assert got.embedding_provider == "ollama"
    assert got.embedding_model == "qwen3-embedding"
    assert got.embedding_url == "http://localhost:11434/api/embed"
    assert got.top_k == 5
