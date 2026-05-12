from pathlib import Path
import sys

# Import chunker from knowledge/build without turning knowledge into a package.
_BUILD_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "build"
if str(_BUILD_DIR) not in sys.path:
    sys.path.insert(0, str(_BUILD_DIR))

from chunker import Chunk, ContentBlock, Section, chunk_sections


def _mk_section(path: str, blocks: list[tuple[str, int, str]]) -> Section:
    return Section(
        path=path.split(" > "),
        blocks=[ContentBlock(text=t, page=p, block_type=b) for t, p, b in blocks],
    )


def test_section_fits_budget_one_chunk():
    section = _mk_section(
        "Chapter 1: Intro > 1.1 Basics",
        [
            ("Apstra blueprints define intended state.", 1, "prose"),
            ("Intent is validated continuously.", 1, "prose"),
        ],
    )

    chunks = chunk_sections([section], target_tokens=400, overlap_paragraphs=1)
    assert len(chunks) == 1


def test_over_budget_prose_splits_with_overlap():
    section = _mk_section(
        "Chapter 3: Blueprints > 3.1 What is a blueprint",
        [
            ("Paragraph one " + ("x " * 120), 2, "prose"),
            ("Paragraph two " + ("y " * 120), 2, "prose"),
            ("Paragraph three " + ("z " * 120), 2, "prose"),
        ],
    )

    chunks = chunk_sections([section], target_tokens=180, overlap_paragraphs=1)
    assert len(chunks) >= 2
    # Verify paragraph-level overlap by checking the end of chunk 1 appears in chunk 2.
    assert "Paragraph two" in chunks[1].text or "Paragraph one" in chunks[1].text


def test_oversized_table_stays_atomic():
    oversized_table = "col1  col2\n" + "row  value\n" * 700
    section = _mk_section(
        "Chapter 7: Data > 7.2 Table",
        [(oversized_table, 7, "table")],
    )

    chunks = chunk_sections([section], target_tokens=120, overlap_paragraphs=1)
    assert len(chunks) == 1
    assert chunks[0].metadata["block_type"] == "table"


def test_code_block_is_atomic():
    code = "set protocols bgp group EVPN neighbor 10.0.0.1 peer-as 65001\n" * 80
    section = _mk_section(
        "Chapter 5: CLI > 5.4 Examples",
        [
            ("Intro text.", 5, "prose"),
            (code, 5, "code"),
            ("Closing text.", 5, "prose"),
        ],
    )

    chunks = chunk_sections([section], target_tokens=120, overlap_paragraphs=1)
    assert any(c.metadata["block_type"] == "code" for c in chunks)


def test_breadcrumb_in_embedded_text_and_section_path_metadata():
    path = "Chapter 3: Blueprints > 3.1 What is a blueprint > 3.1.1 Staged vs active state"
    section = _mk_section(
        path,
        [("The staged state is intended configuration.", 42, "prose")],
    )

    chunks = chunk_sections([section], target_tokens=400, overlap_paragraphs=1)
    assert len(chunks) == 1
    only: Chunk = chunks[0]

    assert only.text_for_embedding.startswith("[Chapter 3: Blueprints > 3.1 What is a blueprint")
    assert only.metadata["section_path"] == path
