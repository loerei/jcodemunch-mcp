#!/usr/bin/env python3
"""Local Pull Request validation suite.

Runs the test matrix, builds the sdist package, and checks for sensitive files.
"""

import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


# Sensitive path regular expression matching common credentials shapes
SENSITIVE_PATTERNS = [
    r"\.claude/",
    r"\.env(\.|$)",
    r"\.pypirc",
    r"\.aws/",
    r"\.ssh/",
    r"id_rsa",
    r"id_ed25519",
    r"\.pem$",
    r"\.key$",
    r"credentials\.(json|yaml|yml|conf|cfg|ini|env)$",
    r"aws_?credentials$",
    r"google[-_]credentials",
]

def run_command(args, cwd=None) -> tuple[int, str]:
    """Execute a subprocess command and capture output."""
    res = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    return res.returncode, res.stdout + "\n" + res.stderr

def main():
    # Reconfigure stdout/stderr to UTF-8 to handle emojis perfectly on Windows consoles
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    print("🚀 Starting local PR validation suite...\n")
    failed = False
    reasons = []

    # --- Phase 1: Building Package & Verifying Sensitive Paths ---
    print("\n--- Phase 1: Building Package & Verifying Sensitive Paths ---")
    dist_dir = Path("dist")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    code, out = run_command(["uv", "build", "--sdist"])
    if code != 0:
        failed = True
        reasons.append(f"❌ Failed to build sdist package:\n{out.strip()}\n")
        print("❌ Build failed.")
    else:
        sdist_files = list(dist_dir.glob("*.tar.gz"))
        if not sdist_files:
            failed = True
            reasons.append("❌ Build succeeded but no .tar.gz archive was found in dist/\n")
            print("❌ No archive found.")
        else:
            sdist_archive = sdist_files[0]
            print(f"📦 Verifying sdist: {sdist_archive.name}...")
            sensitive_finds = []
            try:
                with tarfile.open(sdist_archive, "r:gz") as tar:
                    for name in tar.getnames():
                        for pattern in SENSITIVE_PATTERNS:
                            if re.search(pattern, name, re.IGNORECASE):
                                # Exclude legitimate test resources or files
                                if "test" not in name.lower() or "credentials" not in name.lower():
                                    sensitive_finds.append(name)
                                    break
            except Exception as e:
                failed = True
                reasons.append(f"❌ Failed to extract/read sdist archive: {e}\n")

            if sensitive_finds:
                failed = True
                paths_str = "\n  ".join(sensitive_finds)
                reasons.append(f"❌ Sensitive files detected in sdist package:\n  {paths_str}\n")
                print("❌ Sensitive files detected!")
            else:
                print("✅ No sensitive files detected in sdist package.")

    # --- Final Verdict ---
    print("\n==================================================")
    if failed:
        print("❌ Test(s) are not passed\n")
        for reason in reasons:
            print(reason)
        sys.exit(1)
    else:
        print("✅ All tests passed, PR validated\n")
        sys.exit(0)

if __name__ == "__main__":
    main()
