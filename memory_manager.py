import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any

class MemoryManager:
    """
    Manages the Memory.md file, updating YAML-style blocks within Markdown.
    """
    def __init__(self, memory_file: str = "Memory.md"):
        self.memory_file = Path(memory_file)

    def _update_block(self, content: str, header: str, data: Dict[str, Any]) -> str:
        """
        Finds the YAML block under the given header and updates its values.
        """
        # Find the section starting with the header
        header_pattern = re.compile(rf"## {re.escape(header)}", re.IGNORECASE)
        match = header_pattern.search(content)
        if not match:
            return content

        start_pos = match.start()
        # Find the next YAML block after this header
        yaml_start = content.find("```yaml", start_pos)
        if yaml_start == -1:
            return content
        
        yaml_end = content.find("```", yaml_start + 7)
        if yaml_end == -1:
            return content

        # Extract the existing YAML lines
        block_content = content[yaml_start + 7 : yaml_end].strip()
        lines = block_content.split('\n')
        
        updated_lines = []
        for line in lines:
            if ':' in line:
                key = line.split(':')[0].strip()
                if key in data:
                    val = data[key]
                    # Format value for YAML
                    if isinstance(val, list):
                        formatted_val = f" {json_list_to_yaml(val)}"
                    elif isinstance(val, float):
                        formatted_val = f" {val:.2f}"
                    else:
                        formatted_val = f" {val}"
                    
                    # Try to preserve spacing if possible
                    spacing = line[:line.find(':')]
                    updated_lines.append(f"{spacing}: {formatted_val}")
                    continue
            updated_lines.append(line)

        new_block = "\n".join(updated_lines)
        return content[:yaml_start + 7] + "\n" + new_block + "\n" + content[yaml_end:]

    def update_state(self, state_data: Dict[str, Any]):
        """
        Updates multiple blocks in Memory.md.
        Expects state_data to have keys matching the headers.
        Example: {"Current State": {"last_updated": "..."}, "Today's Stats": {...}}
        """
        if not self.memory_file.exists():
            return

        content = self.memory_file.read_text(encoding='utf-8')
        
        for header, data in state_data.items():
            content = self._update_block(content, header, data)
            
        self.memory_file.write_text(content, encoding='utf-8')

def json_list_to_yaml(lst: list) -> str:
    if not lst:
        return "[]"
    return "[" + ", ".join(f'"{x}"' if isinstance(x, str) else str(x) for x in lst) + "]"
