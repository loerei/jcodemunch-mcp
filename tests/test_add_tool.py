"""Tests for scripts/add_tool.py utility."""

import shutil
import tempfile
from pathlib import Path
import pytest
from scripts.add_tool import scaffold_tool_file, update_server_py, update_config_py, update_test_server_py

@pytest.fixture
def temp_project_root():
    """Create a mock repository layout inside a temporary directory."""
    temp_dir = Path(tempfile.mkdtemp())
    
    # 1. Create directory structure
    (temp_dir / "src/jcodemunch_mcp/tools").mkdir(parents=True, exist_ok=True)
    (temp_dir / "tests").mkdir(parents=True, exist_ok=True)

    # 2. Mock server.py
    mock_server_content = '''
_CANONICAL_TOOL_NAMES: tuple[str, ...] = (
    "index_repo",
    "jcodemunch_guide",
)

def _generate_claude_md_snippet(missing_only: bool = False) -> str:
    categories = [
        ("Utilities", ["invalidate_cache"]),
        ("Self-Guide", ["jcodemunch_guide"]),
    ]
    return ""

async def call_tool(name: str, arguments: dict):
    if name == "index_repo":
        pass
    elif name == "jcodemunch_guide":
        from . import __version__ as _ver
        result = {
            "version": _ver,
            "content": _generate_claude_md_snippet(missing_only=False),
        }
    else:
        result = {"error": f"Unknown tool: {name}"}
'''
    (temp_dir / "src/jcodemunch_mcp/server.py").write_text(mock_server_content, encoding="utf-8")

    # 3. Mock config.py
    mock_config_content = '''
DEFAULTS = {
    "tool_tier_bundles": {
        "core": [
            "index_repo",
        ],
        "standard": [
            "index_repo",
        ],
    },
}

def generate_template() -> str:
    all_tools = sorted([
        "index_repo",
        "jcodemunch_guide",
    ])
    return ""
'''
    (temp_dir / "src/jcodemunch_mcp/config.py").write_text(mock_config_content, encoding="utf-8")

    # 4. Mock test_server.py
    mock_test_content = '''
def test_all_canonical_tools_accounted_in_tier_bundles():
    known_full_only = {
        "jcodemunch_guide",
    }
'''
    (temp_dir / "tests/test_server.py").write_text(mock_test_content, encoding="utf-8")

    yield temp_dir
    
    # Cleanup
    shutil.rmtree(temp_dir)

def test_scaffold_tool_file(temp_project_root):
    """scaffold_tool_file must create a valid placeholder tool file."""
    tool_name = "test_scaffold_tool"
    scaffold_tool_file(tool_name, temp_project_root)
    
    tool_file = temp_project_root / f"src/jcodemunch_mcp/tools/{tool_name}.py"
    assert tool_file.exists()
    
    content = tool_file.read_text(encoding="utf-8")
    assert f"def {tool_name}" in content
    assert "logging.getLogger(\"jcodemunch\")" in content

def test_update_server_py(temp_project_root):
    """update_server_py must register canonical name, category, and dispatcher block."""
    tool_name = "test_scaffold_tool"
    update_server_py(tool_name, "Utilities", temp_project_root)
    
    server_content = (temp_project_root / "src/jcodemunch_mcp/server.py").read_text(encoding="utf-8")
    
    # Assert canonical name exists
    assert f'"{tool_name}",' in server_content
    # Assert category registered
    assert f'"invalidate_cache", "{tool_name}"' in server_content
    # Assert dispatcher block registered
    assert f'elif name == "{tool_name}":' in server_content
    assert f'from .tools.{tool_name} import {tool_name}' in server_content

def test_update_config_py_standard_tier(temp_project_root):
    """update_config_py must register the tool in alphabetical all_tools list and specified tier."""
    tool_name = "test_scaffold_tool"
    update_config_py(tool_name, "standard", temp_project_root)
    
    config_content = (temp_project_root / "src/jcodemunch_mcp/config.py").read_text(encoding="utf-8")
    
    # Assert template list contains name alphabetically
    assert f'"{tool_name}",' in config_content
    # Assert in standard bundle, but not core
    assert f'"index_repo", "{tool_name}"' in config_content
    assert f'"core": [\n            "index_repo",\n        ]' in config_content  # Core remains unchanged

def test_update_config_py_core_tier(temp_project_root):
    """update_config_py must register the tool in both core and standard bundles."""
    tool_name = "test_scaffold_tool"
    update_config_py(tool_name, "core", temp_project_root)
    
    config_content = (temp_project_root / "src/jcodemunch_mcp/config.py").read_text(encoding="utf-8")
    
    # Assert in both core and standard bundles
    assert f'"core": [\n            "index_repo", "{tool_name}"\n        ]' in config_content or f'"index_repo", "{tool_name}"' in config_content

def test_update_test_server_py_full_tier(temp_project_root):
    """update_test_server_py must add the tool to known_full_only whitelist."""
    tool_name = "test_scaffold_tool"
    update_test_server_py(tool_name, "full", temp_project_root)
    
    test_content = (temp_project_root / "tests/test_server.py").read_text(encoding="utf-8")
    assert f'"{tool_name}"' in test_content
