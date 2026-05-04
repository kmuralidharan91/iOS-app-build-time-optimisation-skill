#!/usr/bin/env python3
"""Drift detector: each skills/<name>/{scripts,schemas,references}/<file>
must be byte-identical to the canonical copy under repo-root
{scripts,schemas,references}/<file>.

This script computes a SHA-256 for every canonical file, walks each
skill's bundled copies, compares hashes, and exits 1 with a unified
diff on the first mismatch. Run as a pre-commit hook locally and as a
CI gate when the repo flips public.

Sync strategy rationale + decision: see ``AGENTS.md`` "Sync strategy".
"""

from __future__ import annotations

import hashlib
import pathlib
import sys


CANONICAL_ROOTS = ("scripts", "schemas", "references")
SKILLS_DIRNAME = "skills"


def repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    return here.parent.parent  # scripts/ -> repo root


def hash_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def collect_canonical_hashes(root: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for relative_root in CANONICAL_ROOTS:
        canonical_root = root / relative_root
        if not canonical_root.is_dir():
            continue
        for path in canonical_root.rglob("*"):
            if not path.is_file():
                continue
            if _should_skip(path):
                continue
            relative = path.relative_to(root).as_posix()
            out[relative] = hash_file(path)
    return out


def _should_skip(path: pathlib.Path) -> bool:
    if path.name == ".gitkeep":
        return True
    parts = path.parts
    if "__pycache__" in parts or any(p.startswith(".") for p in parts[-2:]):
        return True
    if path.suffix in (".pyc", ".pyo"):
        return True
    return False


def verify_skills(root: pathlib.Path, canonical: dict[str, str]) -> list[str]:
    """Return a list of human-readable drift entries (empty == clean)."""
    drift: list[str] = []
    skills_root = root / SKILLS_DIRNAME
    if not skills_root.is_dir():
        return drift

    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        for relative_root in CANONICAL_ROOTS:
            sub = skill_dir / relative_root
            if not sub.is_dir():
                continue
            for path in sub.rglob("*"):
                if not path.is_file() or _should_skip(path):
                    continue
                # The skill copy at skills/<skill>/scripts/foo.py mirrors
                # canonical scripts/foo.py.
                relative_inside_skill = path.relative_to(sub).as_posix()
                canonical_key = f"{relative_root}/{relative_inside_skill}"
                expected = canonical.get(canonical_key)
                if expected is None:
                    drift.append(
                        f"{path.relative_to(root)}: NOT in canonical "
                        f"({canonical_key} missing)"
                    )
                    continue
                actual = hash_file(path)
                if actual != expected:
                    drift.append(
                        f"{path.relative_to(root)}: hash mismatch with "
                        f"canonical {canonical_key} "
                        f"(expected {expected[:12]}…, got {actual[:12]}…)"
                    )
    return drift


def main() -> int:
    root = repo_root()
    canonical = collect_canonical_hashes(root)
    drift = verify_skills(root, canonical)
    if drift:
        print("verify-sync: DRIFT detected")
        for entry in drift:
            print(f"  {entry}")
        print(
            f"\nrun: cp <canonical> <skill copy> for each entry above, "
            f"then re-run scripts/verify-sync.py."
        )
        return 1
    print(f"verify-sync: OK ({len(canonical)} canonical files in scope)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
