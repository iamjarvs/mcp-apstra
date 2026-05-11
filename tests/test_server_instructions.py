from unittest.mock import patch

import server


def test_load_instructions_reads_instructions_md():
    text = server._load_instructions()
    assert "MCP server for Juniper Apstra network automation." in text
    assert "read-only" in text


def test_load_instructions_falls_back_when_file_missing_and_logs_warning():
    with patch("server.Path.read_text", side_effect=FileNotFoundError):
        with patch("server.logging.warning") as mock_warning:
            text = server._load_instructions()

    assert "MCP server for Juniper Apstra network automation." in text
    assert "This server is read-only." in text
    assert "get_system_liveness" in text
    assert mock_warning.call_count == 1
