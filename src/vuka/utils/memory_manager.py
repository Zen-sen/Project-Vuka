import os
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock

# A yaml block is only trusted if it appears shortly after its header.
_MAX_BLOCK_LINES = 40


class MemoryManager:
    """
    Manages the Memory.md file, updating YAML-style blocks within Markdown.

    Read-modify-write cycles are serialized with a sibling ``.lock`` file and
    the file itself is written atomically (``.tmp`` + ``os.replace``), so a
    crash mid-write can never leave Memory.md truncated.
    """

    def __init__(self, memory_file: str = "Memory.md"):
        self.memory_file = Path(memory_file)

    def _lock(self) -> FileLock:
        return FileLock(str(self.memory_file) + ".lock")

    def _locate_yaml_block(self, content: str, header: str) -> tuple[int | None, int | None]:
        """Return (start, end) offsets of the first `` ```yaml `` block in the
        ``## <header>`` section, or (None, None).

        A line-based parser tracks fenced code blocks so a ``## Header`` that
        appears inside a code example is ignored, and a header is only matched
        as the exact section title. The block must begin within a few lines of
        the header (guards against matching an unrelated yaml block).
        """
        lines = content.split("\n")
        offsets = []
        pos = 0
        for ln in lines:
            offsets.append(pos)
            pos += len(ln) + 1

        header_lc = header.strip().lower()
        in_fence = False
        section_start: int | None = None
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if stripped.startswith("```"):
                if not in_fence:
                    if section_start is not None and stripped.startswith("```yaml"):
                        if i - section_start > _MAX_BLOCK_LINES:
                            return None, None
                        for j in range(i + 1, len(lines)):
                            if lines[j].strip().startswith("```"):
                                return offsets[i], offsets[j]
                        return None, None
                    in_fence = True
                else:
                    in_fence = False
                continue
            if in_fence:
                continue
            if stripped.startswith("## "):
                if section_start is not None:
                    # Reached the next section without a yaml block here.
                    return None, None
                if stripped[3:].strip().lower() == header_lc:
                    section_start = i
        return None, None

    def _update_block(self, content: str, header: str, data: dict[str, Any]) -> str:
        """
        Finds the YAML block under the given header and updates its values.
        Keys in ``data`` that do not exist yet are appended to the block.
        """
        yaml_start, yaml_end = self._locate_yaml_block(content, header)
        if yaml_start is None:
            return content

        # Extract the existing YAML lines
        block_content = content[yaml_start + 7: yaml_end].strip()
        lines = block_content.split("\n")

        updated_lines = []
        seen_keys = set()
        for line in lines:
            if ":" in line:
                key = line.split(":")[0].strip()
                seen_keys.add(key)
                if key in data:
                    # Preserve the original indentation, replace the value
                    spacing = line[: line.find(":")]
                    updated_lines.append(f"{spacing}:{self._format_value(data[key])}")
                    continue
            updated_lines.append(line)

        # Keys absent from the existing block are appended instead of skipped
        for key, val in data.items():
            if key not in seen_keys:
                updated_lines.append(f"{key}:{self._format_value(val)}")

        new_block = "\n".join(updated_lines)
        return content[:yaml_start + 7] + "\n" + new_block + "\n" + content[yaml_end:]

    def update_state(self, state_data: dict[str, Any]):
        """
        Updates multiple blocks in Memory.md.
        Expects state_data to have keys matching the headers.
        Example: {"Current State": {"last_updated": "..."}, "Today's Stats": {...}}
        """
        if not self.memory_file.exists():
            return

        with self._lock():
            content = self.memory_file.read_text(encoding="utf-8")
            for header, data in state_data.items():
                content = self._update_block(content, header, data)
            self._write_file(content)

    def _write_file(self, content: str) -> None:
        """Atomic write: write to a temp file, then replace the target."""
        tmp = Path(str(self.memory_file) + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, self.memory_file)

    @staticmethod
    def _format_value(val: Any) -> str:
        if isinstance(val, list):
            return f" {MemoryManager.json_list_to_yaml(val)}"
        if isinstance(val, float):
            return f" {val:.2f}"
        return f" {val}"

    @staticmethod
    def json_list_to_yaml(lst: list) -> str:
        """Emit a proper YAML flow sequence (e.g. ``[a, b]``)."""
        return yaml.safe_dump(list(lst), default_flow_style=True).strip()
