#!/usr/bin/env python3
"""
Gen_name.py — Canonical Naming + Library Integrity (Gen4 helpers)

Purpose
-------
Centralize ALL rules that control:
- Canonical filename resolution (case-insensitive + Unicode-stable)
- Naming collision detection
- Strict “NO DUPLICATE IMAGES” policy for the Icon Images library
- Safe copy/move into the library WITHOUT suffixing, renaming, or generating variants

Hard Rule (data integrity)
--------------------------
The system must NEVER create duplicate images in the library.

If two images resolve to the same canonical name:
- Ignore the new one
- Log a warning
- Do not copy, rename, or suffix

What this file should NOT do
----------------------------
- No icon conversion
- No scanning orchestration
- No UI
- No background timers

Where it plugs in
-----------------
- Gen2 should call these helpers inside:
  - mirror_copy_to_icon_images()
  - normalize_icon_images_library()
- Gen1 should NOT attempt to rename/copy/suffix library images; it should call Gen2 only.

Notes on Windows
----------------
Windows paths are effectively case-insensitive. Unicode filenames can also exist in multiple
normalization forms. This module normalizes to NFC and uses casefold keys so that:
  "Anime.PNG" == "anime.png"
  "café.png" (NFC) == "café.png" (NFD)
"""

from __future__ import annotations

import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

LogFn = Optional[Callable[[str], None]]

_BAD_CHARS = '<>:"/\\|?*'


# =============================================================================
# Canonicalization primitives
# =============================================================================

def unicode_nfc(s: str) -> str:
    """Normalize to NFC to reduce visually-identical Unicode duplicates."""
    return unicodedata.normalize("NFC", s or "")


def sanitize_piece(s: str) -> str:
    """
    Make a safe filename-ish component (Windows-safe).

    Important: This function does NOT add suffixes to avoid collisions.
    Collisions are handled strictly elsewhere (skip + log).
    """
    s = unicode_nfc(str(s or ""))
    out = "".join("_" if c in _BAD_CHARS else c for c in s)
    out = out.strip().strip(".")
    return out or "untitled"


def canonical_key(filename: str) -> str:
    """
    Canonical identity key for collision checks:
    - NFC normalize
    - casefold (stronger than lower(); handles more Unicode cases)
    """
    return unicode_nfc(filename).casefold()


def canonicalize_extension(ext: str) -> str:
    """
    Canonicalize an extension:
    - ensure leading dot
    - lowercase
    - sanitize
    - NFC normalize
    """
    ext = unicode_nfc(str(ext or ""))
    if not ext:
        return ""
    if not ext.startswith("."):
        ext = "." + ext
    body = sanitize_piece(ext[1:]).lower()
    return "." + unicode_nfc(body)


def canonical_library_filename(src_name: str, *, allowed_exts: Iterable[str]) -> str:
    """
    Deterministically produce the library filename for a source name.

    Policy:
    - stem is sanitized + NFC normalized (but we keep readable intent)
    - extension is forced lowercase and sanitized
    - if src has no extension: return stem only (caller decides whether that is acceptable)
    - if extension exists but is not in allowed_exts: we still return a canonical filename;
      caller should reject unsupported types before calling library ops.
    """
    allowed = {str(e).lower() for e in allowed_exts}
    src_name = unicode_nfc(src_name)
    p = Path(src_name)

    stem = unicode_nfc(sanitize_piece(p.stem))
    ext = canonicalize_extension(p.suffix)

    # Keep deterministic output even for odd extensions (caller filters upstream)
    if ext and allowed and ext not in allowed:
        pass

    return f"{stem}{ext}" if ext else stem


def flatten_name_from_subfolder(rel_path_parts: Tuple[str, ...]) -> str:
    """
    Create a flattened name based on a nested relative path.

    Example:
      ("SomeFolder", "anime.png") -> "SomeFolder__anime.png"

    This is used by “normalize/flatten” to keep some origin context without generating duplicates.
    Final collision handling is still strict skip+log.
    """
    if not rel_path_parts:
        return "untitled"
    if len(rel_path_parts) == 1:
        return sanitize_piece(rel_path_parts[0])

    folder = sanitize_piece(rel_path_parts[-2])
    fname = sanitize_piece(rel_path_parts[-1])
    return f"{folder}__{fname}"


# =============================================================================
# Collision reporting
# =============================================================================

@dataclass(frozen=True)
class Collision:
    incoming: Path
    desired_name: str
    existing: Path

    def message(self, *, op: str) -> str:
        return (
            f"{op}: SKIP (collision — duplicates forbidden): "
            f"incoming='{self.incoming.name}' -> desired='{self.desired_name}' "
            f"but existing='{self.existing.name}'"
        )


# =============================================================================
# Library indexing
# =============================================================================

def build_library_index(library_dir: Path, *, is_accepted_file) -> Dict[str, Path]:
    """
    Map canonical_key(filename) -> Path for files in library_dir (top-level only).

    This is the core “one-to-one mapping” enforcement mechanism:
      - All comparisons are done via canonical_key(name).
    """
    library_dir = Path(library_dir)
    out: Dict[str, Path] = {}
    try:
        for p in library_dir.iterdir():
            if p.is_file() and is_accepted_file(p):
                out[canonical_key(p.name)] = p
    except Exception:
        pass
    return out


# =============================================================================
# Strict library operations (NO DUPLICATES)
# =============================================================================

def copy_into_library_strict(
    src: Path,
    library_dir: Path,
    *,
    allowed_exts: Iterable[str],
    is_accepted_file,
    logfn: LogFn = None,
) -> Tuple[Optional[Path], Optional[Collision]]:
    """
    Copy src into library_dir under a canonical filename.

    Behavior:
    - If src does not exist / not a file / not accepted: returns (None, None)
    - Compute desired canonical filename FIRST
    - If canonical collision exists: SKIP + log + return (existing_path, Collision)
      (Returning existing_path is intentional: caller can treat this as “library already has it”.)
    - Else copy2 into library_dir/desired_name and return (new_path, None)

    HARD RULE:
    - Never creates anime (2).png, anime2.png, etc.
    """
    src = Path(src)
    library_dir = Path(library_dir)

    if not (src.exists() and src.is_file()):
        return None, None
    if not is_accepted_file(src):
        return None, None

    library_dir.mkdir(parents=True, exist_ok=True)

    desired_name = canonical_library_filename(src.name, allowed_exts=allowed_exts)
    key = canonical_key(desired_name)

    index = build_library_index(library_dir, is_accepted_file=is_accepted_file)
    existing = index.get(key)
    if existing is not None:
        col = Collision(incoming=src, desired_name=desired_name, existing=existing)
        if logfn:
            logfn(col.message(op="LIBRARY COPY"))
        return existing, col

    dst = library_dir / desired_name
    try:
        shutil.copy2(src, dst)
        if logfn:
            logfn(f"LIBRARY COPY: OK '{src.name}' -> '{dst.name}'")
        return dst, None
    except Exception as e:
        if logfn:
            logfn(f"LIBRARY COPY: FAILED '{src}' -> '{dst}': {type(e).__name__}: {e}")
        return None, None


def move_into_library_strict(
    src: Path,
    library_dir: Path,
    *,
    desired_name: Optional[str] = None,
    allowed_exts: Iterable[str],
    is_accepted_file,
    logfn: LogFn = None,
) -> Tuple[Optional[Path], Optional[Collision]]:
    """
    Move src into library_dir under a canonical filename.

    Intended for “normalize/flatten” phases.

    Behavior:
    - If collision exists: SKIP + log; leave src in place; return (existing_path, Collision)
    - Else move (Path.replace) into canonical name; return (new_path, None)

    HARD RULE:
    - Never creates suffix variants for images in the library.
    """
    src = Path(src)
    library_dir = Path(library_dir)

    if not (src.exists() and src.is_file()):
        return None, None
    if not is_accepted_file(src):
        return None, None

    library_dir.mkdir(parents=True, exist_ok=True)

    if desired_name is None:
        desired_name = canonical_library_filename(src.name, allowed_exts=allowed_exts)
    else:
        desired_name = canonical_library_filename(desired_name, allowed_exts=allowed_exts)

    key = canonical_key(desired_name)

    index = build_library_index(library_dir, is_accepted_file=is_accepted_file)
    existing = index.get(key)
    if existing is not None:
        col = Collision(incoming=src, desired_name=desired_name, existing=existing)
        if logfn:
            logfn(col.message(op="LIBRARY MOVE"))
        return existing, col

    dst = library_dir / desired_name
    try:
        src.replace(dst)
        if logfn:
            logfn(f"LIBRARY MOVE: OK '{src.name}' -> '{dst.name}'")
        return dst, None
    except Exception as e:
        if logfn:
            logfn(f"LIBRARY MOVE: FAILED '{src}' -> '{dst}': {type(e).__name__}: {e}")
        return None, None


# =============================================================================
# Optional: orphan/quarantine helper for non-library usage
# =============================================================================

def unique_path_for_quarantine(p: Path) -> Path:
    """
    Generate a non-colliding path by adding ' (2)', ' (3)', ...

    IMPORTANT:
    - This is ONLY for quarantine/trash buckets (like Icons/_Orphans).
    - Do NOT use this to create alternate names for library images.
    """
    p = Path(p)
    if not p.exists():
        return p
    parent = p.parent
    stem = p.stem
    suf = p.suffix
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suf}"
        if not cand.exists():
            return cand
        i += 1
