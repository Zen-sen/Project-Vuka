"""MemoryManager tests — atomic writes, cross-process lock, missing-key append."""
from pathlib import Path

from vuka.utils.memory_manager import MemoryManager

SAMPLE = (
    "# Vuka Memory\n"
    "\n"
    "## Current State\n"
    "\n"
    "```yaml\n"
    "last_updated: 2026-01-01\n"
    "equity: 10000.00\n"
    "```\n"
    "\n"
    "## Today's Stats\n"
    "\n"
    "```yaml\n"
    "wins: 3\n"
    "```\n"
)


def _make(tmp_path) -> Path:
    f = tmp_path / "Memory.md"
    f.write_text(SAMPLE, encoding="utf-8")
    return f


class TestUpdateBlock:
    def test_updates_existing_key_and_preserves_others(self, tmp_path):
        f = _make(tmp_path)
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"equity": 10123.45}})
        text = f.read_text(encoding="utf-8")
        assert "equity: 10123.45" in text
        assert "last_updated: 2026-01-01" in text
        assert "wins: 3" in text  # other block untouched

    def test_appends_missing_keys(self, tmp_path):
        f = _make(tmp_path)
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"max_drawdown_pct": 1.5, "peak": 12000.0}})
        text = f.read_text(encoding="utf-8")
        assert "max_drawdown_pct: 1.50" in text
        assert "peak: 12000.00" in text

    def test_formats_lists(self, tmp_path):
        f = tmp_path / "Memory.md"
        f.write_text(
            "# Vuka\n\n## Current State\n\n```yaml\nsessions: []\n```\n",
            encoding="utf-8",
        )
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"sessions": ["London Open", "NY"]}})
        text = f.read_text(encoding="utf-8")
        assert "sessions: [London Open, NY]" in text

    def test_no_crash_when_file_missing(self, tmp_path):
        mm = MemoryManager(str(tmp_path / "nope.md"))
        mm.update_state({"Current State": {"equity": 1.0}})

    def test_header_inside_code_fence_is_ignored(self, tmp_path):
        """A `## Header` line inside a code example must not be treated as a section."""
        f = tmp_path / "Memory.md"
        f.write_text(
            "# Vuka\n\n"
            "## Current State\n\n"
            "```yaml\n"
            "equity: 100.00\n"
            "```\n\n"
            "```python\n"
            "## Current State\n"
            "x = 1\n"
            "```\n",
            encoding="utf-8",
        )
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"equity": 200.0}})
        text = f.read_text(encoding="utf-8")
        # The real section's yaml block was updated, and the python fence
        # (which contained its own `## Current State` line) is untouched.
        assert "equity: 200.00" in text
        assert "x = 1" in text
        assert '```yaml\nequity: 200.00\n```' in text

    def test_header_requires_exact_section_title(self, tmp_path):
        f = tmp_path / "Memory.md"
        f.write_text(
            "# Vuka\n\n## Current State Extended\n\n```yaml\nx: 1\n```\n",
            encoding="utf-8",
        )
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"equity": 5.0}})
        # No matching header -> no new keys are injected anywhere.
        assert "equity" not in f.read_text(encoding="utf-8")

    def test_yaml_block_far_from_header_not_matched(self, tmp_path):
        f = tmp_path / "Memory.md"
        f.write_text(
            "# Vuka\n\n## Current State\n\n"
            + "\n".join(f"line {i}: filler" for i in range(60))
            + "\n\n```yaml\nequity: 1.00\n```\n",
            encoding="utf-8",
        )
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"equity": 2.0}})
        assert "equity: 2.00" not in f.read_text(encoding="utf-8")


class TestAtomicWrite:
    def test_no_tmp_file_left_behind(self, tmp_path):
        f = _make(tmp_path)
        mm = MemoryManager(str(f))
        mm.update_state({"Current State": {"equity": 9999.99}})
        assert not Path(str(f) + ".tmp").exists()
        assert "equity: 9999.99" in f.read_text(encoding="utf-8")

    def test_lock_acquired_around_read_modify_write(self, tmp_path):
        from unittest.mock import MagicMock, patch
        f = _make(tmp_path)
        mm = MemoryManager(str(f))
        fake = MagicMock()
        fake.__enter__.return_value = mm._lock()
        fake.__exit__.return_value = False
        with patch.object(mm, "_lock", return_value=fake):
            mm.update_state({"Current State": {"equity": 5.0}})
        fake.__enter__.assert_called_once()
        fake.__exit__.assert_called_once()
        assert "equity: 5.00" in f.read_text(encoding="utf-8")
