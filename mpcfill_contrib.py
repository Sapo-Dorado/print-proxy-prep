#!/usr/bin/env python3
"""Prepare MPC Autofill (mpcfill.com) contribution images from Scryfall.

Fetches card images from Scryfall and adds the 35px bleed margin required
for mpcfill contributions, producing 816×1110px PNG files named per the
mpcfill contribution guidelines.

mpcfill standard: 816×1110px = 300 DPI (DPI_HEIGHT_RATIO = 300/1110)
Source: https://github.com/chilli-axe/mpc-autofill

Usage:
    python mpcfill_contrib.py "Oroku Saki, Shredder Rising"
    python mpcfill_contrib.py "Lightning Bolt" "Counterspell" -o ./output
    python mpcfill_contrib.py --set tmt --number 68
    python mpcfill_contrib.py "Lightning Bolt" --tag "Extended Art"
"""

import argparse
import os
import sys
import urllib.request
import urllib.parse
import json

from PIL import Image

# ---------------------------------------------------------------------------
# MPC Autofill constants
# Source: MPCAutofill/cardpicker/sources/update_database.py
#   DPI_HEIGHT_RATIO = 300 / 1110  # 300 DPI for image of vertical resolution 1110 pixels
# ---------------------------------------------------------------------------
MPC_WIDTH = 816
MPC_HEIGHT = 1110

# Scryfall PNG dimensions (full card, no bleed)
SCRYFALL_WIDTH = 745
SCRYFALL_HEIGHT = 1040

# Bleed added on each side: (816-745)//2 = 35px, (1110-1040)//2 = 35px
BLEED_PX = 35

SCRYFALL_API = "https://api.scryfall.com"


# ---------------------------------------------------------------------------
# Scryfall API
# ---------------------------------------------------------------------------
SCRYFALL_HEADERS = {
    "User-Agent": "mpcfill-contrib/1.0",
    "Accept": "application/json",
}


def scryfall_get(url: str) -> dict:
    req = urllib.request.Request(url, headers=SCRYFALL_HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def find_card_fuzzy(name: str) -> dict:
    """Fetch card data by fuzzy name match."""
    url = f"{SCRYFALL_API}/cards/named?{urllib.parse.urlencode({'fuzzy': name})}"
    return scryfall_get(url)


def find_card_by_set(set_code: str, collector_number: str) -> dict:
    """Fetch card data by set code and collector number."""
    url = f"{SCRYFALL_API}/cards/{set_code.lower()}/{collector_number}"
    return scryfall_get(url)


def get_png_url(card: dict) -> str | None:
    """Extract the PNG image URL from a card object (handles DFCs)."""
    # Normal single-faced card
    if "image_uris" in card:
        return card["image_uris"].get("png")
    # Double-faced / multi-faced: use the front face
    if "card_faces" in card:
        for face in card["card_faces"]:
            if "image_uris" in face:
                return face["image_uris"].get("png")
    return None


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------
def sample_border_color(img: Image.Image, sample_size: int = 8) -> tuple:
    """Sample the average colour of the top-left corner to use as bleed fill.

    For most MTG cards this will be black (0, 0, 0).
    """
    pixels = []
    w, h = img.size
    for x in range(min(sample_size, w)):
        for y in range(min(sample_size, h)):
            px = img.getpixel((x, y))
            pixels.append(px[:3])  # drop alpha if present
    r = sum(p[0] for p in pixels) // len(pixels)
    g = sum(p[1] for p in pixels) // len(pixels)
    b = sum(p[2] for p in pixels) // len(pixels)
    return (r, g, b, 255)


def add_bleed(img: Image.Image) -> Image.Image:
    """Add bleed edges to a Scryfall PNG, producing an MPC-ready 816×1110 image.

    The source image is centered on a canvas filled with the card's border
    colour (sampled from the corners). If the source is not exactly
    745×1040, it is scaled to fit within 816×1110 while preserving aspect
    ratio, then padded to fill the remainder.
    """
    src_w, src_h = img.size

    # If source is already the right size, just return
    if src_w == MPC_WIDTH and src_h == MPC_HEIGHT:
        return img.convert("RGBA")

    border_color = sample_border_color(img)
    canvas = Image.new("RGBA", (MPC_WIDTH, MPC_HEIGHT), border_color)

    # Scale source to fit within MPC canvas if it's larger or a different ratio
    scale = min(MPC_WIDTH / src_w, MPC_HEIGHT / src_h)
    if scale < 1.0 or (src_w != SCRYFALL_WIDTH or src_h != SCRYFALL_HEIGHT):
        new_w = round(src_w * scale)
        new_h = round(src_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        src_w, src_h = img.size

    x_off = (MPC_WIDTH - src_w) // 2
    y_off = (MPC_HEIGHT - src_h) // 2
    canvas.paste(img, (x_off, y_off))
    return canvas


def mpcfill_filename(card_name: str, tag: str | None = None) -> str:
    """Produce the correct mpcfill contribution filename.

    Naming rules (from mpcfill contribution guidelines):
    - Double-faced card names use " & " separator (handled by caller passing the right name)
    - Tags go in parentheses after the name
    - File extension: .png

    The "Scryfall Scan" tag is a recognised alias in mpcfill for the
    "Upscaled Scan" tag, making the source clearly identifiable.
    """
    effective_tag = tag if tag else "Scryfall Scan"
    return f"{card_name} ({effective_tag}).png"


# ---------------------------------------------------------------------------
# Core: process one card
# ---------------------------------------------------------------------------
def process_card(card: dict, out_dir: str, tag: str | None = None, verbose: bool = True) -> str | None:
    """Download, add bleed, and save a single card. Returns the output path."""
    name = card.get("name", "Unknown")
    png_url = get_png_url(card)
    if not png_url:
        print(f"  ERROR: No PNG image available for '{name}'", file=sys.stderr)
        return None

    if verbose:
        set_code = card.get("set", "???").upper()
        number = card.get("collector_number", "?")
        print(f"  Fetching {name} [{set_code} #{number}]...")

    # Download PNG into memory
    req = urllib.request.Request(png_url, headers=SCRYFALL_HEADERS)
    with urllib.request.urlopen(req) as resp:
        import io
        raw = io.BytesIO(resp.read())

    img = Image.open(raw)
    src_size = img.size
    img_with_bleed = add_bleed(img)

    filename = mpcfill_filename(name, tag)
    out_path = os.path.join(out_dir, filename)
    img_with_bleed.save(out_path, "PNG")

    size_kb = os.path.getsize(out_path) / 1024
    if verbose:
        print(f"  Saved: {filename}")
        print(f"    Source: {src_size[0]}×{src_size[1]}px → MPC: {MPC_WIDTH}×{MPC_HEIGHT}px "
              f"(bleed: {BLEED_PX}px/side) — {size_kb:.0f} KB")

    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare MPC Autofill contribution images from Scryfall.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "Oroku Saki, Shredder Rising"
  %(prog)s "Lightning Bolt" "Counterspell" "Brainstorm"
  %(prog)s --set tmt --number 68
  %(prog)s "Thalia, Guardian of Thraben" --tag "Extended Art"

Output:
  Files are named "<Card Name> (Scryfall Scan).png" by default, matching
  the mpcfill "Upscaled Scan" tag alias so the source is clearly identified.

  To contribute: upload the output folder to a public Google Drive folder,
  then submit it to mpcfill via their Discord or contact chilli_axe.
  See: https://mpcfill.com/contributions
        """,
    )
    parser.add_argument(
        "cards",
        nargs="*",
        metavar="CARD_NAME",
        help="Card name(s) to fetch (fuzzy matched via Scryfall)",
    )
    parser.add_argument(
        "--set", "-s",
        metavar="SET_CODE",
        help="Set code for exact lookup (use with --number)",
    )
    parser.add_argument(
        "--number", "-n",
        metavar="COLLECTOR_NUMBER",
        help="Collector number for exact lookup (use with --set)",
    )
    parser.add_argument(
        "--tag", "-t",
        default=None,
        metavar="TAG",
        help='Tag to append in parentheses (default: "Scryfall Scan")',
    )
    parser.add_argument(
        "--output", "-o",
        default="./mpcfill",
        metavar="DIR",
        help="Output directory (default: ./mpcfill)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Validate inputs
    exact_lookup = args.set or args.number
    if exact_lookup and not (args.set and args.number):
        parser.error("--set and --number must be used together")
    if not args.cards and not exact_lookup:
        parser.print_help()
        sys.exit(1)
    if args.cards and exact_lookup:
        parser.error("Cannot combine card name arguments with --set/--number")

    verbose = not args.quiet
    os.makedirs(args.output, exist_ok=True)

    # Build list of (lookup_fn, display_label) tasks
    tasks = []
    if exact_lookup:
        tasks.append((lambda: find_card_by_set(args.set, args.number),
                       f"{args.set.upper()} #{args.number}"))
    else:
        for name in args.cards:
            tasks.append((lambda n=name: find_card_fuzzy(n), name))

    results = {"ok": [], "failed": []}

    for i, (fetch_fn, label) in enumerate(tasks):
        if verbose and len(tasks) > 1:
            print(f"[{i+1}/{len(tasks)}] {label}")
        try:
            card = fetch_fn()
            out_path = process_card(card, args.output, tag=args.tag, verbose=verbose)
            if out_path:
                results["ok"].append(out_path)
            else:
                results["failed"].append(label)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  ERROR: Card not found: '{label}'", file=sys.stderr)
            else:
                print(f"  ERROR: HTTP {e.code} fetching '{label}': {e}", file=sys.stderr)
            results["failed"].append(label)
        except Exception as e:
            print(f"  ERROR processing '{label}': {e}", file=sys.stderr)
            results["failed"].append(label)

    # Summary
    if verbose:
        print()
        print(f"Done: {len(results['ok'])} image(s) saved to {args.output}/")
        if results["failed"]:
            print(f"Failed ({len(results['failed'])}):", ", ".join(results["failed"]))
        if results["ok"]:
            print()
            print("Next steps:")
            print("  1. Upload the output folder to a public Google Drive folder")
            print("  2. Submit to mpcfill via Discord or contact chilli_axe")
            print("     https://mpcfill.com/contributions")


if __name__ == "__main__":
    main()
