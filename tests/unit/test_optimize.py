from __future__ import annotations

from pathlib import Path

from memdex.config import Thresholds
from memdex.models import SourceMode
from memdex.optimize.categorize import categorize, is_obsolete, score_importance
from memdex.optimize.compress import compress, first_sentence
from memdex.optimize.dedupe import find_duplicates
from tests.conftest import unit


class TestDedupe:
    def test_exact_duplicate_is_shadowed(self):
        a = unit("Database", "We use PostgreSQL 16 for everything.")
        b = unit("Database copy", "we use postgresql 16 for everything!!")
        result = find_duplicates([a, b], threshold=0.85)
        assert len(result.exact) == 1
        assert not (a.is_live and b.is_live)
        assert a.is_live  # the first in stable order wins

    def test_near_duplicate_merges_above_threshold(self):
        a = unit(
            "Deploys",
            "Deploys go through GitHub Actions and update the ECS service definition.",
        )
        b = unit(
            "Deployment",
            "Deploys go through GitHub Actions and update the ECS service definitions.",
        )
        result = find_duplicates([a, b], threshold=0.85)
        assert len(result.near) == 1
        assert a.merged_from  # the winner records where the other came from

    def test_similar_but_distinct_is_reported_not_merged(self):
        a = unit("Testing", "Run make test for unit tests before every pull request please.")
        b = unit("Linting", "Run make lint for static checks before every pull request please.")
        result = find_duplicates([a, b], threshold=0.99)
        assert result.near == []
        assert a.is_live and b.is_live
        assert result.similar  # surfaced in the compact report

    def test_managed_beats_readonly(self):
        readonly = unit("A", "Same content here for both of them.", source_mode=SourceMode.READONLY)
        readonly.source_file = Path(".claude/CLAUDE.md")
        managed = unit("A", "Same content here for both of them.")
        find_duplicates([readonly, managed], threshold=0.85)
        assert managed.is_live
        assert not readonly.is_live

    def test_code_differences_prevent_a_merge(self):
        a = unit("Deploy", "Run this:\n\n```bash\nmake deploy ENV=production\n```")
        b = unit("Deploy", "Run this:\n\n```bash\nmake deploy ENV=staging\n```")
        result = find_duplicates([a, b], threshold=0.99)
        assert result.exact == []
        assert a.is_live and b.is_live


class TestCompress:
    def test_drops_conversational_filler(self):
        body = "Sure, here's what I know!\n\nWe use PostgreSQL.\n\nHope this helps!"
        assert compress(body) == "We use PostgreSQL."

    def test_is_a_fixed_point(self):
        body = (
            "Okay!\n\n\n\nWe use PostgreSQL 16.   \n\n\nMigrations run with goose.\n\n"
            "We use PostgreSQL 16.\n"
        )
        once = compress(body)
        assert compress(once) == once

    def test_never_touches_fence_contents(self):
        body = "Text.\n\n```python\n\n\nx = 1   \n# Sure, here's what I know!\n```\n"
        out = compress(body)
        assert "\n\n\nx = 1   \n# Sure, here's what I know!" in out

    def test_removes_repeated_paragraph(self):
        para = "Deploys go through GitHub Actions and update the ECS service definition."
        body = f"{para}\n\nSomething else entirely.\n\n{para}"
        assert compress(body).count(para) == 1

    def test_keeps_short_repeats(self):
        body = "Run it.\n\nSomething else.\n\nRun it."
        assert compress(body).count("Run it.") == 2

    def test_never_drops_technical_lines(self):
        body = "Always wrap errors with fmt.Errorf(\"doing x: %w\", err)."
        assert compress(body) == body


class TestDescriptions:
    def test_joins_wrapped_lines_into_one_sentence(self):
        body = (
            "The staging environment mirrors production but runs a single\n"
            "replica of each service. More text."
        )
        assert first_sentence(body) == (
            "The staging environment mirrors production but runs a single replica of each service."
        )

    def test_preserves_identifiers(self):
        assert "tenant_id" in first_sentence("Events carry a `tenant_id` pair. More.")

    def test_unwraps_emphasis_and_links(self):
        assert first_sentence("See **the [docs](http://x) page**.") == "See the docs page."

    def test_skips_headings_and_fences(self):
        body = "## Heading\n\n```\ncode\n```\n\nThe real first sentence lives here."
        assert first_sentence(body) == "The real first sentence lives here."

    def test_code_only_memory_describes_itself_with_its_first_command(self):
        body = "```bash\nuv sync --all-extras\nuv run pytest\n```"
        assert first_sentence(body) == "`uv sync --all-extras`"

    def test_code_fallback_skips_comments_and_fences(self):
        body = "```bash\n# a comment\nmake deploy\n```"
        assert first_sentence(body) == "`make deploy`"

    def test_empty_body_has_no_description(self):
        assert first_sentence("") == ""

    def test_truncates_long_sentences(self):
        long = "word " * 60
        out = first_sentence(long)
        assert len(out) <= 121 and out.endswith("…")


class TestCategorize:
    def test_keyword_categories(self):
        memory = unit("Database schema", "We use PostgreSQL with row level security.")
        assert categorize(memory) == "architecture"
        assert categorize(unit("Chroma selection", "We chose Chroma over ES.")) == "decisions"
        assert categorize(unit("Testing", "Run make test before every commit.")) == "development"

    def test_short_keywords_do_not_match_inside_words(self):
        # "ci" lives inside "decisions" and "sequencing" — it must not count.
        memory = unit("Event sequencing", "Consumers deduplicate on the pair.")
        memory.breadcrumb = ("Decisions",)
        assert categorize(memory) == "decisions"

    def test_plural_headings_still_match(self):
        memory = unit("Phoenix", "Phoenix replaces the legacy extraction job.")
        memory.breadcrumb = ("Projects",)
        assert categorize(memory) == "projects"

    def test_unknown_content_is_general(self):
        assert categorize(unit("Zzz", "Lorem ipsum dolor sit amet.")) == "general"

    def test_obsolete_detection_is_report_only(self):
        memory = unit("Sessions", "This is deprecated — replaced by JWTs.")
        assert is_obsolete(memory)
        assert memory.is_live  # flagged, never deleted

    def test_importance_stays_in_range(self):
        memory = unit("Decision", "We must never do this. ```x```")
        memory.category = "decisions"
        assert 0.05 <= score_importance(memory) <= 0.95


class TestSplitOversized:
    def test_splits_only_at_headings(self):
        from memdex.optimize.structure import split_oversized
        from memdex.tokens import HeuristicCounter

        body = "## A\n\n" + ("alpha " * 200) + "\n\n## B\n\n" + ("beta " * 200)
        memory = unit("Big", body)
        pieces = split_oversized(memory, Thresholds(max_file_tokens=100), HeuristicCounter())
        assert [p.title for p in pieces] == ["A", "B"]

    def test_locked_units_are_never_resplit(self):
        from memdex.optimize.structure import split_oversized
        from memdex.tokens import HeuristicCounter

        memory = unit("Big", "## A\n\n" + ("alpha " * 200) + "\n\n## B\n\nbeta")
        memory.frontmatter_locked = True
        pieces = split_oversized(memory, Thresholds(max_file_tokens=10), HeuristicCounter())
        assert pieces == [memory]

    def test_leaf_without_headings_stays_whole(self):
        from memdex.optimize.structure import split_oversized
        from memdex.tokens import HeuristicCounter

        memory = unit("Big", "alpha " * 500)
        pieces = split_oversized(memory, Thresholds(max_file_tokens=10), HeuristicCounter())
        assert pieces == [memory]


class TestScaffoldingStripping:
    def test_html_comments_are_removed(self):
        assert compress("Real fact.\n\n<!-- a hint for the assistant -->\n") == "Real fact."

    def test_multiline_comment_blocks_are_removed(self):
        body = "<!--\nfill me\nplease\n-->\n\nActual memory content here."
        assert compress(body) == "Actual memory content here."

    def test_inline_comments_keep_surrounding_text(self):
        assert compress("Before <!-- gone --> after.") == "Before  after."

    def test_comments_inside_fences_are_code(self):
        body = "```html\n<!-- keep me -->\n```"
        assert compress(body) == body

    def test_placeholders_are_removed(self):
        assert compress("_(none yet)_") == ""
        assert compress("- _(none yet)_") == ""

    def test_comment_stripping_is_a_fixed_point(self):
        body = "<!-- x -->\nReal.\n\n_(none yet)_\n\n<!--\nblock\n-->"
        once = compress(body)
        assert compress(once) == once == "Real."
