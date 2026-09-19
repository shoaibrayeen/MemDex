from __future__ import annotations

from pathlib import Path

from memdex.config import Thresholds
from memdex.models import INDEX_MARKER, SourceFile, SourceMode
from memdex.optimize.structure import tree_to_units
from memdex.parsing import (
    is_generated_index,
    node_text,
    parse_tree,
    read_memdex_frontmatter,
    split_frontmatter,
    strip_title_heading,
    title_for_file,
)
from memdex.tokens import HeuristicCounter


def source(text: str, path: str = "MEMORY.md", mode: SourceMode = SourceMode.MANAGED) -> SourceFile:
    return SourceFile(path=Path(path), mode=mode, raw_text=text)


def units_for(text: str, min_unit_tokens: int = 20):
    thresholds = Thresholds(min_unit_tokens=min_unit_tokens)
    return tree_to_units(source(text), parse_tree(text), thresholds, HeuristicCounter())


class TestFrontmatter:
    def test_parses_leading_block(self):
        data, raw, body = split_frontmatter("---\ntitle: A\n---\n# Heading\n")
        assert data == {"title": "A"}
        assert raw.startswith("---")
        assert body == "# Heading"

    def test_horizontal_rule_is_not_frontmatter(self):
        text = "---\n\nJust a rule, never closed as frontmatter."
        data, raw, body = split_frontmatter(text)
        assert data is None and raw == "" and body == text

    def test_malformed_yaml_degrades_to_body(self):
        text = "---\ntitle: [unclosed\n---\ncontent\n"
        data, _, body = split_frontmatter(text)
        assert data is None
        assert body == text

    def test_dots_close_frontmatter(self):
        data, _, body = split_frontmatter("---\nname: x\n...\nbody\n")
        assert data == {"name": "x"}
        assert body == "body"

    def test_memdex_namespace_requires_valid_id(self):
        assert read_memdex_frontmatter({"memdex": {"id": "nope"}}) is None
        meta = read_memdex_frontmatter(
            {"memdex": {"id": "mem_abc12345", "title": "T", "category": "decisions"}}
        )
        assert meta["id"] == "mem_abc12345"
        assert meta["category"] == "decisions"

    def test_unknown_category_falls_back(self):
        meta = read_memdex_frontmatter({"memdex": {"id": "mem_1", "category": "nonsense"}})
        assert meta is None or meta["category"] == "general"


class TestFences:
    def test_heading_inside_fence_does_not_split(self):
        text = "# Real\n\n```md\n# Fake heading\n```\n\ntail\n"
        root = parse_tree(text)
        assert len(root.children) == 1
        assert "# Fake heading" in node_text(root.children[0])

    def test_tilde_fences(self):
        text = "# Real\n\n~~~\n## Not a heading\n~~~\n"
        root = parse_tree(text)
        assert root.children[0].children == []

    def test_longer_closing_fence(self):
        text = "# A\n\n````\n```\n# still code\n````\n\n# B\n"
        root = parse_tree(text)
        assert [c.heading for c in root.children] == ["A", "B"]

    def test_headingless_file_is_one_unit(self):
        units = units_for("Just some notes about staging.\nNothing more.\n")
        assert len(units) == 1
        assert units[0].title == "Memory"  # from the file stem MEMORY.md


class TestStructure:
    def test_splits_to_leaf_topics_with_breadcrumbs(self):
        text = (
            "# Architecture\n\n"
            "## Database\n\nWe use PostgreSQL 16 as the primary store for every tenant,\n"
            "with row level security enabled so a missing filter fails closed.\n\n"
            "## Vector search\n\nWe use Chroma for semantic search over support tickets,\n"
            "running locally in development and as a sidecar in production.\n"
        )
        units = units_for(text)
        titles = sorted(u.title for u in units)
        assert titles == ["Database", "Vector search"]
        assert all(u.breadcrumb == ("Architecture",) for u in units)

    def test_small_sections_fold_into_parent(self):
        text = "# Parent\n\nIntro sentence that is long enough to matter here.\n\n## Tiny\n\nx\n"
        units = units_for(text, min_unit_tokens=50)
        assert len(units) == 1
        assert "## Tiny" in units[0].body

    def test_document_title_is_not_a_breadcrumb(self):
        text = (
            "# Project Memory\n\n"
            "## Decisions\n\n"
            "### Event sequencing\n\nEvents carry a monotonic sequence pair assigned by\n"
            "Postgres, and consumers deduplicate on it so redelivery is always safe.\n"
        )
        units = units_for(text)
        assert units[0].breadcrumb == ("Decisions",)
        assert "Project Memory" not in units[0].provenance.split(" > ")[1:]

    def test_provenance_records_the_path(self):
        text = (
            "# Architecture\n\n## Database\n\n"
            "We use PostgreSQL 16 as the primary store for every tenant, with row\n"
            "level security enabled so that a missing filter fails closed.\n"
        )
        units = units_for(text)
        assert units[0].provenance == "MEMORY.md > Architecture > Database"


class TestMisc:
    def test_marker_detection(self):
        assert is_generated_index(f"{INDEX_MARKER} -->\n# Memory Index\n", INDEX_MARKER)
        assert not is_generated_index("# Memory\n", INDEX_MARKER)

    def test_strip_title_heading(self):
        assert strip_title_heading("# Title\n\nbody\n", "Title") == "body"
        assert strip_title_heading("# Other\n\nbody\n", "Title") == "# Other\n\nbody"

    def test_title_for_file(self):
        assert title_for_file(Path("api-notes.md"), None) == "Api Notes"
        assert title_for_file(Path("x.md"), {"title": "Real Title"}) == "Real Title"
