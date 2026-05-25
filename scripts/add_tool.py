#!/usr/bin/env python3
"""Developer utility to scaffold and register a new MCP tool in the codebase."""

import argparse
import os
import re
import sys
from pathlib import Path

def scaffold_tool_file(name: str, root: Path):
    """Create the scaffolded implementation file for the new tool."""
    path = root / f"src/jcodemunch_mcp/tools/{name}.py"
    if path.exists():
        print(f"[-] Tool file {path} already exists. Skipping scaffolding.")
        return
    
    content = f'''"""Implementation of {name} tool."""
import logging
from typing import Any, Optional

logger = logging.getLogger("jcodemunch")

def {name}(repo: str, storage_path: Optional[str] = None, **kwargs) -> dict[str, Any]:
    """Execute the {name} tool.

    Args:
        repo: Repository identifier.
        storage_path: Path to database storage.
    """
    logger.info("Executing {name} for repo %s", repo)
    # TODO: Implement tool business logic
    return {{"success": True, "results": []}}
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[+] Created scaffold tool: {path}")

def update_server_py(name: str, category: str, root: Path):
    """Update canonical names, categories list, and dispatcher inside server.py."""
    server_path = root / "src/jcodemunch_mcp/server.py"
    if not server_path.exists():
        print(f"[-] server.py not found at {server_path}. Skipping registration.")
        return
    
    content = server_path.read_text(encoding="utf-8")

    # 1. Update _CANONICAL_TOOL_NAMES
    if f'"{name}"' in content or f"'{name}'" in content:
        print("[-] Tool name already present in server.py canonical list.")
    else:
        # jcodemunch_guide is always at the end of the tuple
        guide_pattern = '"jcodemunch_guide",'
        if guide_pattern in content:
            replacement = f'{guide_pattern}\n    "{name}",'
            content = content.replace(guide_pattern, replacement, 1)
            print("[+] Registered in _CANONICAL_TOOL_NAMES")
        else:
            print("[-] Could not find 'jcodemunch_guide' marker in _CANONICAL_TOOL_NAMES.")

    # 2. Update Categories list in _generate_claude_md_snippet
    idx = content.find(f'"{category}", [')
    if idx == -1:
        idx = content.find(f"'{category}', [")
    if idx == -1:
        idx = content.find(f'"{category}"')
    
    if idx != -1:
        end_bracket = content.find(']', idx)
        if end_bracket != -1:
            list_str = content[idx:end_bracket]
            last_quote = list_str.rfind('"')
            if last_quote == -1:
                last_quote = list_str.rfind("'")
            if last_quote != -1:
                insert_pos = idx + last_quote + 1
                content = content[:insert_pos] + f', "{name}"' + content[insert_pos:]
                print(f"[+] Registered under category '{category}' in CLAUDE.md snippet")
            else:
                print(f"[-] Could not find list elements inside category '{category}'")
        else:
            print(f"[-] Could not locate closing bracket for category '{category}'")
    else:
        print(f"[-] Category '{category}' not found. You may need to add it manually to _generate_claude_md_snippet.")

    # 3. Update call_tool() dispatcher
    dispatcher_marker = 'elif name == "jcodemunch_guide":'
    if dispatcher_marker in content:
        idx = content.find(dispatcher_marker)
        if idx != -1:
            # Find start of line to detect indentation
            line_start = content.rfind("\n", 0, idx) + 1
            indentation = " " * (idx - line_start)
            
            # The body of the elif block is indented by 4 extra spaces
            body_indent = indentation + "    "
            
            # Construct dispatcher_end dynamically with matched indentation
            dispatcher_end = f"""{body_indent}result = {{
{body_indent}    "version": _ver,
{body_indent}    "content": _generate_claude_md_snippet(missing_only=False),
{body_indent}}}"""
            
            end_block_idx = content.find(dispatcher_end, idx)
            if end_block_idx != -1:
                insert_pos = end_block_idx + len(dispatcher_end)
                dispatcher_block = f'''
{indentation}elif name == "{name}":
{indentation}    from .tools.{name} import {name}
{indentation}    result = {name}(
{indentation}        repo=arguments.get("repo"),
{indentation}        storage_path=storage_path,
{indentation}    )'''
                content = content[:insert_pos] + dispatcher_block + content[insert_pos:]
                print("[+] Registered inside call_tool() dispatcher")
            else:
                print("[-] Could not find jcodemunch_guide dispatcher block end.")
        else:
            print("[-] Could not find jcodemunch_guide dispatcher marker.")
    else:
        print("[-] Could not find dispatcher marker in server.py.")

    server_path.write_text(content, encoding="utf-8")

def update_config_py(name: str, tier: str, root: Path):
    """Update config template and tier bundles in config.py."""
    config_path = root / "src/jcodemunch_mcp/config.py"
    if not config_path.exists():
        print(f"[-] config.py not found at {config_path}. Skipping registration.")
        return
    
    content = config_path.read_text(encoding="utf-8")

    # 1. Update generate_template() alphabetically
    start_idx = content.find("all_tools = sorted([")
    if start_idx != -1:
        end_idx = content.find("])", start_idx)
        if end_idx != -1:
            block = content[start_idx + len("all_tools = sorted([") : end_idx]
            tools = [t.strip().strip('"').strip("'") for t in block.split(",") if t.strip()]
            if name not in tools:
                tools.append(name)
                tools = sorted(list(set(tools)))
                new_block = "\n" + "\n".join(f'        "{t}",' for t in tools) + "\n    "
                content = content[:start_idx + len("all_tools = sorted([")] + new_block + content[end_idx:]
                print("[+] Registered alphabetically in config template all_tools")
            else:
                print("[-] Already present in config template all_tools.")
        else:
            print("[-] Could not locate config template all_tools block end.")
    else:
        print("[-] Could not locate config template all_tools block.")

    # 2. Update Tier Bundles in DEFAULTS
    def add_to_config_list(file_content, list_key):
        list_marker = f'"{list_key}": ['
        s_idx = file_content.find(list_marker)
        if s_idx == -1:
            list_marker = f"'{list_key}': ["
            s_idx = file_content.find(list_marker)
        if s_idx != -1:
            e_idx = file_content.find("]", s_idx)
            if e_idx != -1:
                block_content = file_content[s_idx + len(list_marker) : e_idx]
                existing_tools = [t.strip().strip('"').strip("'") for t in block_content.split(",") if t.strip()]
                if name not in existing_tools:
                    last_q = block_content.rfind('"')
                    if last_q == -1:
                        last_q = block_content.rfind("'")
                    if last_q != -1:
                        ins_pos = s_idx + len(list_marker) + last_q + 1
                        file_content = file_content[:ins_pos] + f', "{name}"' + file_content[ins_pos:]
                        print(f"[+] Registered in DEFAULTS['tool_tier_bundles']['{list_key}']")
                    else:
                        print(f"[-] Could not find element quotes in '{list_key}' bundle")
                else:
                    print(f"[-] Already present in '{list_key}' tier bundle.")
            else:
                print(f"[-] Could not locate closing bracket for '{list_key}' list.")
        else:
            print(f"[-] Could not find '{list_key}' tier bundle in DEFAULTS.")
        return file_content

    if tier == "core":
        content = add_to_config_list(content, "core")
        content = add_to_config_list(content, "standard")
    elif tier == "standard":
        content = add_to_config_list(content, "standard")
    
    config_path.write_text(content, encoding="utf-8")

def update_test_server_py(name: str, tier: str, root: Path):
    """Update test whitelist in test_server.py if the tool is full-tier only."""
    test_path = root / "tests/test_server.py"
    if not test_path.exists():
        print(f"[-] test_server.py not found at {test_path}. Skipping registration.")
        return
    
    content = test_path.read_text(encoding="utf-8")

    # If tier is full-only, we must add it to known_full_only whitelisting in test_server.py
    if tier == "full":
        start_idx = content.find("known_full_only = {")
        if start_idx != -1:
            end_idx = content.find("}", start_idx)
            if end_idx != -1:
                block = content[start_idx + len("known_full_only = {") : end_idx]
                if f'"{name}"' not in block and f"'{name}'" not in block:
                    last_q = block.rfind('"')
                    if last_q == -1:
                        last_q = block.rfind("'")
                    if last_q != -1:
                        ins_pos = start_idx + len("known_full_only = {") + last_q + 1
                        content = content[:ins_pos] + f',\n        "{name}"' + content[ins_pos:]
                        print("[+] Registered in test_server.py known_full_only whitelist")
                    else:
                        print("[-] Could not find element quotes in known_full_only whitelist.")
                else:
                    print("[-] Already present in known_full_only whitelist.")
            else:
                print("[-] Could not locate known_full_only block end.")
        else:
            print("[-] Could not locate known_full_only block.")
    
    test_path.write_text(content, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(
        description="Helper script to scaffold and register a new tool across all files."
    )
    parser.add_argument("name", help="Name of the new tool (snake_case)")
    parser.add_argument(
        "--category",
        default="Utilities",
        help="CLAUDE.md categories snippet section (default: Utilities)"
    )
    parser.add_argument(
        "--tier",
        choices=["core", "standard", "full"],
        default="full",
        help="Active tool tier profile (default: full)"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory of the project workspace"
    )

    args = parser.parse_args()

    name = args.name.strip().lower()
    if not re.match(r"^[a-z0-9_]+$", name):
        print(f"Error: Invalid tool name '{name}'. Must be snake_case only (lowercase, numbers, underscores).")
        sys.exit(1)

    root = Path(args.root).resolve()
    print(f"[*] Project Root: {root}")
    print(f"[*] Registering new tool '{name}' (Category: '{args.category}', Tier: '{args.tier}')...")

    # Run steps
    scaffold_tool_file(name, root)
    update_server_py(name, args.category, root)
    update_config_py(name, args.tier, root)
    update_test_server_py(name, args.tier, root)

    print(f"[+] Successfully scaffolded and registered new tool '{name}'!")
    print("[*] Don't forget to implement custom arguments, description, and unit tests.")

if __name__ == "__main__":
    main()
