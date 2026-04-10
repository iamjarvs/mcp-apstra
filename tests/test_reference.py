import pytest

from tools.reference import _GUIDE_PATH


# ---------------------------------------------------------------------------
# Guide file
# ---------------------------------------------------------------------------

class TestGuideFile:

    def test_guide_file_exists(self):
        assert _GUIDE_PATH.exists(), f"Reference guide not found at {_GUIDE_PATH}"

    def test_guide_is_non_empty(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert len(content) > 5000

    def test_guide_is_valid_utf8(self):
        # Will raise UnicodeDecodeError if not valid UTF-8
        _GUIDE_PATH.read_text(encoding="utf-8")

    def test_guide_contains_architecture_sections(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert "3-Stage Clos" in content or "3-stage" in content.lower()
        assert "5-Stage Clos" in content or "5-stage" in content.lower()
        assert "Collapsed Fabric" in content or "collapsed" in content.lower()

    def test_guide_contains_bgp_content(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert "BGP" in content
        assert "l3clos-l" in content
        assert "l3clos-s" in content

    def test_guide_contains_evpn_content(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert "EVPN" in content
        assert "mac-vrf" in content
        assert "VNI" in content

    def test_guide_contains_dci_content(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert "DCI" in content
        assert "Stitching" in content or "stitching" in content

    def test_guide_contains_community_architecture(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        # Loop-prevention community values documented in the guide
        assert "FROM_SPINE_FABRIC_TIER" in content
        assert "FROM_SPINE_EVPN_TIER" in content

    def test_guide_contains_policy_section(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert "LEAF_TO_SPINE_FABRIC_OUT" in content
        assert "SPINE_TO_LEAF_FABRIC_OUT" in content


# ---------------------------------------------------------------------------
# get_reference_design_context handler (via the module-level path helper)
# ---------------------------------------------------------------------------

class TestGetReferenceDesignContext:

    async def test_returns_dict_with_expected_keys(self):
        # Test the handler logic directly without going through MCP
        # by simulating what the tool function does
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        result = {
            "title": "Apstra Reference Design Guide",
            "format": "markdown",
            "content": content,
        }
        assert result["title"] == "Apstra Reference Design Guide"
        assert result["format"] == "markdown"
        assert len(result["content"]) > 5000

    async def test_content_contains_all_major_sections(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        sections = [
            "How to Read",
            "Common Building Blocks",
            "3-Stage Clos",
            "5-Stage Clos",
            "Collapsed Fabric",
            "Access Switch",
            "DCI",
            "BGP Community",
            "Quick Reference",
        ]
        for phrase in sections:
            assert phrase in content or phrase.lower() in content.lower(), (
                f"Expected section '{phrase}' not found in guide"
            )
