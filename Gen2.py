#!/usr/bin/env python3
# Gen2.py — IconMaker engine (no UI)
#
# Goals:
# - Clean, predictable folder layout
# - Robust image scan + conversion (PNG/JPG/WEBP/BMP/TIFF + SVG via CairoSVG)
# - Multi-size .ico generation that does NOT "stick" to 16x16:
#     - Always saves from a LARGE base frame
#     - Embeds all requested sizes via Pillow's ICO writer
# - Rename-safe orphan cleanup:
#     - If source image "Alion.png" is removed/renamed, "Alion.ico" is deleted (or quarantined)
# - Defensive output handling:
#     - If caller mistakenly passes a *.ico file path where an output directory is expected,
#       we treat it as an explicit output file path and do NOT create a folder named "*.ico".
#
# NOTE (Windows reality):
# - Windows shell generally *uses* up to 256x256 for icons.
# - You *can* embed sizes above 256 in an ICO, but Windows may ignore them.
# - If you want true 1024 quality in real usage, export a separate 1024 PNG too.
#
# Updated for: 8..1024 sizes, safer out paths, better ICO save behavior, clearer diagnostics.
#
# NEW (Progress callbacks):
# - scan_icon_images_and_convert() now supports progress_cb per-image
# - convert_many() supports progress_cb per-image
#   Signature: progress_cb(phase: str, index: int, total: int, path: Optional[Path]) -> None
#
#   phase values (by convention):
#     - "normalize"   (0/1 markers)
#     - "orphans"     (0/1 markers)
#     - "scan"        (enumerating / filtering)
#     - "convert"     (per-image conversion)
#     - "done"        (0/1 markers)
#
# This makes the UI progress bar non-cosmetic without rewriting the engine.

from __future__ import annotations

import argparse
import math
import shutil
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError

__all__ = [
    "IMAGE_EXTS",
    "DEFAULT_SIZES",
    "AUTO_FULL_SIZES",
    "ICONER_ROOT",
    "ICON_IMAGES_DIR",
    "ICONS_DIR",
    "DEFAULT_OUTPUT_DIR",
    "PADDING_PRESETS",
    "ScanReport",
    "parse_sizes",
    "find_images",
    "make_ico",
    "sanitize_piece",
    "closest_folder_named_filename",
    "unique_path",
    "normalize_icon_images_library",
    "mirror_copy_to_icon_images",
    "list_missing_icon_tasks",
    "convert_many",
    "scan_icon_images_and_convert",
    "remove_orphan_icons",
]

# =========================
# Standard paths & constants
# =========================

IMAGE_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".svg"}

DEFAULT_SIZES: List[int] = [16, 24, 32, 48, 64, 128, 256]

AUTO_FULL_SIZES: List[int] = list(range(8, 1025, 8))

ICONER_ROOT = Path.home() / "Desktop" / "Iconer"
ICON_IMAGES_DIR = ICONER_ROOT / "Icon Images"
ICONS_DIR = ICON_IMAGES_DIR / "Icons"

for _d in (ICONER_ROOT, ICON_IMAGES_DIR, ICONS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_OUTPUT_DIR = str(ICONS_DIR)

PADDING_PRESETS = {
    "tight": 0.96,
    "balanced": 0.88,
    "extra": 0.80,
}

# Progress callback type
ProgressCB = Callable[[str, int, int, Optional[Path]], None]


# =========================
# Small utilities
# =========================

def _is_image_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMAGE_EXTS


def parse_sizes(s: Optional[str]) -> Optional[List[int]]:
    """
    Parse a comma-separated list of sizes (ints). Returns None if blank or invalid.
    Examples: "16,24,32" -> [16,24,32]
    """
    if not s:
        return None
    out: List[int] = []
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            n = int(piece)
            if n > 0:
                out.append(n)
        except ValueError:
            continue
    return out or None


def sanitize_piece(s: str) -> str:
    """Make a safe filename-ish component."""
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad else c for c in s)
    out = out.strip().strip(".")
    return out or "untitled"


def closest_folder_named_filename(p: Path) -> str:
    """
    If p is nested under Icon Images subfolders, create a flattened name:
      SomeFolder__file.png
    If already in root, keep file name.
    """
    try:
        rel = p.relative_to(ICON_IMAGES_DIR)
    except Exception:
        return p.name

    parts = list(rel.parts)
    if len(parts) <= 1:
        return p.name

    folder = sanitize_piece(parts[0])
    fname = sanitize_piece(p.stem) + p.suffix.lower()
    return f"{folder}__{fname}"


def unique_path(p: Path) -> Path:
    """Return a non-colliding path by adding ' (2)', ' (3)', ... if needed."""
    if not p.exists():
        return p
    stem = p.stem
    suf = p.suffix
    parent = p.parent
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suf}"
        if not cand.exists():
            return cand
        i += 1


def find_images(folder: Path, recursive: bool = True) -> List[Path]:
    """Find supported images under folder."""
    if not folder.exists():
        return []
    if recursive:
        return [p for p in folder.rglob("*") if _is_image_file(p)]
    return [p for p in folder.iterdir() if _is_image_file(p)]


# =========================
# CairoSVG import handling
# =========================

def _try_import_cairosvg() -> Tuple[Optional[object], Optional[str]]:
    """
    Returns (cairosvg_module_or_None, error_message_or_None).
    Provides a clear diagnostic on failure.
    """
    try:
        import cairosvg  # type: ignore
        return cairosvg, None
    except Exception as e:
        exe = sys.executable
        prefix = getattr(sys, "prefix", "")
        base_prefix = getattr(sys, "base_prefix", "")
        venv_note = ""
        if base_prefix and prefix and (prefix != base_prefix):
            venv_note = " (venv detected: sys.prefix != sys.base_prefix)"

        msg = (
            "SVG support requires 'cairosvg' in the SAME Python environment as the running app.\n"
            f"Running Python: {exe}\n"
            f"sys.prefix: {prefix}{venv_note}\n"
            f"Import error: {type(e).__name__}: {e}\n"
            "Fix:\n"
            "  - If running from source: install into the same interpreter you launch IconMaker with.\n"
            "  - If running a built EXE: CairoSVG must be bundled into the EXE; pip installing into a venv won't affect the EXE.\n"
        )
        return None, msg


def _rasterize_svg_to_rgba(svg_path: Path) -> Image.Image:
    cairosvg, err = _try_import_cairosvg()
    if cairosvg is None:
        raise RuntimeError(err or "CairoSVG not available")

    png_bytes = cairosvg.svg2png(url=str(svg_path))
    im = Image.open(BytesIO(png_bytes)).convert("RGBA")
    return im


# =========================
# Image processing primitives
# =========================

def _autocrop_alpha(im: Image.Image) -> Image.Image:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    alpha = im.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return im
    return im.crop(bbox)


def _pad_to_square_rgba(im: Image.Image, content_scale: float) -> Image.Image:
    if im.mode != "RGBA":
        im = im.convert("RGBA")

    w, h = im.size
    if w <= 0 or h <= 0:
        return im

    max_side = max(w, h)

    content_scale = max(0.01, float(content_scale))
    side = int(math.ceil(max_side / content_scale))
    side = max(side, 1)

    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    x = (side - w) // 2
    y = (side - h) // 2
    canvas.paste(im, (x, y), im)
    return canvas


def _load_image_any(path: Path) -> Image.Image:
    suf = path.suffix.lower()
    if suf == ".svg":
        return _rasterize_svg_to_rgba(path)
    return Image.open(path).convert("RGBA")


def _normalize_sizes(sizes: Optional[Sequence[int]]) -> List[int]:
    if not sizes:
        return []
    norm: List[int] = []
    seen = set()
    for s in sizes:
        try:
            n = int(s)
        except Exception:
            continue
        if n <= 0:
            continue
        if n in seen:
            continue
        seen.add(n)
        norm.append(n)
    norm.sort()
    return norm


def _resolve_output_target(
    outdir_or_file: Path,
    *,
    src: Path,
    suffix: str = "",
) -> Tuple[Path, Path]:
    p = Path(outdir_or_file)

    if p.suffix.lower() == ".ico":
        out_dir = p.parent
        out_name = p.name
        return out_dir, out_dir / out_name

    out_dir = p
    out_name = f"{src.stem}{suffix}.ico"
    return out_dir, out_dir / out_name


# =========================
# ICO generation
# =========================

def make_ico(
    src: Path,
    outdir: Path,
    *,
    sizes: Optional[Sequence[int]] = None,
    suffix: str = "",
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "balanced",
    logfn: Callable[[str], None] | None = None,
) -> Tuple[bool, str]:
    src = Path(src)
    if not src.exists() or not src.is_file():
        return False, f"ERR: Source does not exist: {src}"

    out_dir, out_path = _resolve_output_target(Path(outdir), src=src, suffix=suffix)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"ERR: Cannot create output directory {out_dir}: {e}"

    if out_path.exists() and not overwrite:
        return True, f"SKIP: {src.name} -> {out_path.name} (exists)"

    sizes_to_use = _normalize_sizes(sizes if sizes is not None else DEFAULT_SIZES)
    if not sizes_to_use:
        return False, f"ERR: No valid sizes for {src.name}"

    padding_mode = (padding_mode or "balanced").strip().lower()
    content_scale = PADDING_PRESETS.get(padding_mode, PADDING_PRESETS["balanced"])

    try:
        im = _load_image_any(src)
    except RuntimeError as e:
        return False, f"ERR: Failed to open {src.name}: {e}"
    except UnidentifiedImageError as e:
        return False, f"ERR: Unrecognized image file {src.name}: {e}"
    except Exception as e:
        return False, f"ERR: Failed to open {src.name}: {e}"

    if autocrop:
        try:
            im = _autocrop_alpha(im)
        except Exception:
            pass

    if keep_alpha:
        im = im.convert("RGBA")
    else:
        im = im.convert("RGBA").convert("RGB")

    if keep_alpha:
        base_canvas = _pad_to_square_rgba(im, content_scale=content_scale)
    else:
        w, h = im.size
        max_side = max(w, h)
        side = int(math.ceil(max_side / max(0.01, float(content_scale))))
        side = max(side, 1)
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        x = (side - w) // 2
        y = (side - h) // 2
        canvas.paste(im, (x, y))
        base_canvas = canvas

    largest = max(sizes_to_use)

    try:
        if keep_alpha:
            base = base_canvas.resize((largest, largest), Image.LANCZOS).convert("RGBA")
        else:
            base = base_canvas.resize((largest, largest), Image.LANCZOS).convert("RGB")

        base.save(out_path, format="ICO", sizes=[(s, s) for s in sizes_to_use])

        msg = f"OK: {src.name} -> {out_path.name} sizes={sizes_to_use[0]}..{sizes_to_use[-1]} ({len(sizes_to_use)} frames)"
        if logfn:
            logfn(msg)
        return True, msg
    except Exception as e:
        return False, f"ERR: Failed to write ICO {out_path}: {e}"


# =========================
# Library normalization
# =========================

def normalize_icon_images_library(logfn: Callable[[str], None] | None = None) -> int:
    """
    Flatten nested files into ICON_IMAGES_DIR root WITHOUT creating duplicates.

    Rule:
      - If the target filename already exists in ICON_IMAGES_DIR, DO NOT move and DO NOT rename.
      - Never create ' (2)' variants.
      - Leave the conflicting file where it is (and log).
    """
    if not ICON_IMAGES_DIR.exists():
        return 0

    moved = 0
    for p in ICON_IMAGES_DIR.rglob("*"):
        if not _is_image_file(p):
            continue
        if p.parent == ICON_IMAGES_DIR:
            continue

        new_name = closest_folder_named_filename(p)
        dst = ICON_IMAGES_DIR / new_name

        if dst.exists():
            if logfn:
                logfn(f"Normalize: SKIP (name collision): {p} -> {dst.name}")
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            p.replace(dst)
            moved += 1
        except Exception as e:
            if logfn:
                logfn(f"Normalize: FAILED {p} -> {dst}: {e}")

    if moved and logfn:
        logfn(f"Normalize: moved {moved} file(s) into root.")
    return moved

def mirror_copy_to_icon_images(src: Path, logfn: Callable[[str], None] | None = None) -> Optional[Path]:
    """
    COPY src into ICON_IMAGES_DIR (canonical library) without ever creating duplicates.

    Rule:
      - If the destination name already exists in ICON_IMAGES_DIR, SKIP the copy.
      - Never create ' (2)', ' (3)' variants.
    """
    src = Path(src)
    if not src.exists() or not src.is_file():
        return None

    ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    dst = ICON_IMAGES_DIR / sanitize_piece(src.name)

    if dst.exists():
        if logfn:
            logfn(f"Mirror: SKIP (already exists): {dst.name}")
        return dst  # treat as success: the library already has it

    try:
        shutil.copy2(src, dst)
        if logfn:
            logfn(f"Mirror: COPIED {src.name} -> {dst.name}")
        return dst
    except Exception as e:
        if logfn:
            logfn(f"Mirror: FAILED {src} -> {dst}: {e}")
        return None

# =========================
# Orphan cleanup
# =========================

def remove_orphan_icons(
    images_dir: Path,
    icons_dir: Path,
    *,
    suffix: str = "",
    action: str = "delete",
    logfn: Callable[[str], None] | None = None,
) -> int:
    action = (action or "").strip().lower()
    if action not in {"delete", "quarantine"}:
        action = "delete"

    images_dir = Path(images_dir)
    icons_dir = Path(icons_dir)

    try:
        icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return 0

    src_stems: set[str] = set()
    try:
        for img in images_dir.iterdir():
            if _is_image_file(img):
                src_stems.add(img.stem)
    except Exception:
        pass

    removed = 0
    orphan_dir = icons_dir / "_Orphans"

    for ico in icons_dir.glob("*.ico"):
        try:
            stem = ico.stem

            if suffix:
                if not stem.endswith(suffix):
                    continue
                base_stem = stem[: -len(suffix)]
            else:
                base_stem = stem

            if base_stem in src_stems:
                continue

            if action == "quarantine":
                orphan_dir.mkdir(parents=True, exist_ok=True)
                target = unique_path(orphan_dir / ico.name)
                ico.replace(target)
                if logfn:
                    logfn(f"Orphan: moved {ico.name} -> {target.name}")
            else:
                ico.unlink(missing_ok=True)
                if logfn:
                    logfn(f"Orphan: deleted {ico.name}")

            removed += 1
        except Exception as e:
            if logfn:
                logfn(f"Orphan: failed to remove {ico.name}: {e}")

    return removed


# =========================
# Task planning
# =========================

def list_missing_icon_tasks(images_dir: Path, outdir: Path, suffix: str = "") -> List[Path]:
    images_dir = Path(images_dir)
    outdir = Path(outdir)

    missing: List[Path] = []
    if not images_dir.exists():
        return missing

    for img in images_dir.iterdir():
        if not _is_image_file(img):
            continue
        ico = outdir / f"{img.stem}{suffix}.ico"
        if not ico.exists():
            missing.append(img)

    return missing


# =========================
# Batch conversion
# =========================

def convert_many(
    images: Iterable[Path],
    outdir: Path,
    *,
    sizes: Optional[Sequence[int]] = None,
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "balanced",
    suffix: str = "",
    logfn: Callable[[str], None] | None = None,
    progress_cb: ProgressCB | None = None,
    progress_phase: str = "convert",
    skip_if_ico_exists: bool = False,
) -> Tuple[int, int, int]:
    """
    Convert multiple images to ICO.

    Returns: (scanned, converted, errors)

    skip_if_ico_exists:
      - If True and the target .ico already exists, SKIP conversion for that image.
      - This is stronger than overwrite=False and is intended for maintenance runs.
    """
    imgs: List[Path] = []
    for img in images:
        p = Path(img)
        if _is_image_file(p):
            imgs.append(p)

    total = len(imgs)
    scanned = 0
    converted = 0
    errors = 0

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for idx, img in enumerate(imgs, start=1):
        scanned += 1
        if progress_cb:
            try:
                progress_cb(progress_phase, idx, total, img)
            except Exception:
                pass

        # Hard skip rule for maintenance: if ico exists, do nothing
        if skip_if_ico_exists:
            ico_path = outdir / f"{img.stem}{suffix}.ico"
            if ico_path.exists():
                if logfn:
                    logfn(f"SKIP: {img.name} (icon exists: {ico_path.name})")
                continue

        ok, msg = make_ico(
            img,
            outdir,
            sizes=sizes,
            overwrite=overwrite,
            keep_alpha=keep_alpha,
            autocrop=autocrop,
            padding_mode=padding_mode,
            suffix=suffix,
            logfn=logfn,
        )
        if ok:
            if msg.startswith("OK:"):
                converted += 1
        else:
            errors += 1
            if logfn:
                logfn(msg)

    return scanned, converted, errors



@dataclass(frozen=True)
class ScanReport:
    scanned: int
    converted: int
    errors: int
    orphan_icons_removed: int
    normalized_moves: int


def scan_icon_images_and_convert(
    *,
    sizes: Optional[Sequence[int]] = None,
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "balanced",
    remove_orphans: bool = True,
    orphan_action: str = "delete",
    suffix: str = "",
    logfn: Callable[[str], None] | None = None,
    progress_cb: ProgressCB | None = None,
) -> ScanReport:
    """
    High-level "library scan":

    1) Normalize Icon Images library (flatten subfolders into root).
    2) Remove orphan .ico in Icons folder (rename-safe).
    3) Convert each root-level image into Icons/<stem>.ico (multi-size).

    Progress:
      - normalize/orphans as 0/1 markers
      - convert is per-image real progress
    """
    if progress_cb:
        try:
            progress_cb("normalize", 0, 1, None)
        except Exception:
            pass

    normalized_moves = normalize_icon_images_library(logfn=logfn)

    if progress_cb:
        try:
            progress_cb("normalize", 1, 1, None)
        except Exception:
            pass

    orphan_removed = 0
    if remove_orphans:
        if progress_cb:
            try:
                progress_cb("orphans", 0, 1, None)
            except Exception:
                pass

        orphan_removed = remove_orphan_icons(
            images_dir=ICON_IMAGES_DIR,
            icons_dir=ICONS_DIR,
            suffix=suffix,
            action="quarantine" if str(orphan_action).lower() in ("quarantine", "trash", "move") else "delete",
            logfn=logfn,
        )

        if progress_cb:
            try:
                progress_cb("orphans", 1, 1, None)
            except Exception:
                pass

    # Convert root-level library images
    if progress_cb:
        try:
            progress_cb("scan", 0, 1, None)
        except Exception:
            pass

    images = [p for p in ICON_IMAGES_DIR.iterdir() if _is_image_file(p)]

    if progress_cb:
        try:
            progress_cb("scan", 1, 1, None)
        except Exception:
            pass

    scanned, converted, errors = convert_many(
        images,
        ICONS_DIR,
        sizes=sizes if sizes is not None else AUTO_FULL_SIZES,
        overwrite=overwrite,
        keep_alpha=keep_alpha,
        autocrop=autocrop,
        padding_mode=padding_mode,
        suffix=suffix,
        logfn=logfn,
        progress_cb=progress_cb,
        progress_phase="convert",
        skip_if_ico_exists=True,  # <-- ENFORCE: if ico exists, skip
    )

    if logfn:
        logfn(
            f"Scan done. scanned={scanned} converted={converted} errors={errors} "
            f"orphan_removed={orphan_removed} normalized_moves={normalized_moves}"
        )

    if progress_cb:
        try:
            progress_cb("done", 1, 1, None)
        except Exception:
            pass

    return ScanReport(
        scanned=scanned,
        converted=converted,
        errors=errors,
        orphan_icons_removed=orphan_removed,
        normalized_moves=normalized_moves,
    )


# =========================
# CLI (optional dev tool)
# =========================

def _cli() -> int:
    ap = argparse.ArgumentParser(description="IconMaker engine (Gen2) — batch icon generator")
    ap.add_argument("input", nargs="?", default="", help="File or folder. If blank, scans Icon Images library.")
    ap.add_argument("--out", default=DEFAULT_OUTPUT_DIR, help="Output directory (default: Icon Images/Icons)")
    ap.add_argument("--sizes", default="", help="Comma sizes: 16,24,32 or blank for full 8..1024 step 8")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing .ico")
    ap.add_argument("--no-overwrite", dest="overwrite", action="store_false", help="Do not overwrite (skip existing)")
    ap.set_defaults(overwrite=True)
    ap.add_argument("--autocrop", action="store_true", help="Autocrop transparent margins before padding")
    ap.add_argument("--padding", default="balanced", choices=list(PADDING_PRESETS.keys()), help="Padding preset")
    ap.add_argument("--remove-orphans", action="store_true", help="Delete orphan icons in output folder")
    ap.add_argument("--quarantine-orphans", action="store_true", help="Move orphan icons into Icons/_Orphans")
    ap.add_argument("--suffix", default="", help="Optional suffix added to output icon name (before .ico)")

    ns = ap.parse_args()
    sizes = parse_sizes(ns.sizes) or AUTO_FULL_SIZES

    def _print(s: str) -> None:
        print(s)

    outdir = Path(ns.out)

    if ns.input:
        p = Path(ns.input)
        if p.is_file():
            imgs = [p] if _is_image_file(p) else []
        elif p.is_dir():
            imgs = find_images(p, recursive=True)
        else:
            imgs = []

        scanned, converted, errors = convert_many(
            imgs,
            outdir,
            sizes=sizes,
            overwrite=bool(ns.overwrite),
            keep_alpha=True,
            autocrop=bool(ns.autocrop),
            padding_mode=str(ns.padding),
            suffix=str(ns.suffix),
            logfn=_print,
            progress_cb=None,
        )

        if ns.remove_orphans:
            action = "quarantine" if ns.quarantine_orphans else "delete"
            removed = remove_orphan_icons(
                images_dir=ICON_IMAGES_DIR,
                icons_dir=outdir,
                suffix=str(ns.suffix),
                action=action,
                logfn=_print,
            )
            _print(f"Orphan removed: {removed}")

        _print(f"Done. scanned={scanned} converted={converted} errors={errors}")
        return 0 if errors == 0 else 2

    report = scan_icon_images_and_convert(
        sizes=sizes,
        overwrite=bool(ns.overwrite),
        autocrop=bool(ns.autocrop),
        padding_mode=str(ns.padding),
        remove_orphans=bool(ns.remove_orphans),
        orphan_action="quarantine" if ns.quarantine_orphans else "delete",
        suffix=str(ns.suffix),
        logfn=_print,
        progress_cb=None,
    )
    return 0 if report.errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(_cli())
