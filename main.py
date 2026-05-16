#!/usr/bin/env python3
"""CLI tool for preparing print-ready proxy card PDFs from XML order files."""

import argparse
import os
import random
import shutil
import sys
import time
import xml.etree.ElementTree as ET

from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import letter, A4, legal
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# Paths – defaults are relative to this script, overridable via CLI / env
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VIBRANCE_CUBE = os.path.join(SCRIPT_DIR, "vibrance.CUBE")

PAGE_SIZES = {
    "letter": letter,
    "a4": A4,
    "legal": legal,
}

CARD_W = 2.48 * 72  # points
CARD_H = 3.46 * 72  # points


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        description="Prepare print-ready proxy card PDFs from an XML order file."
    )
    parser.add_argument(
        "xml_file",
        nargs="?",
        default=None,
        help="Path to the XML order file",
    )
    parser.add_argument(
        "-o", "--output",
        default="./output",
        help="Base output directory (default: ./output)",
    )
    parser.add_argument(
        "--paper",
        choices=["letter", "a4", "legal"],
        default="letter",
        help="Page size (default: letter)",
    )
    parser.add_argument(
        "--orientation",
        choices=["portrait", "landscape"],
        default="portrait",
        help="Page orientation (default: portrait)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=1200,
        help="Max DPI before downscaling (default: 1200)",
    )
    parser.add_argument(
        "--vibrance",
        action="store_true",
        help="Apply vibrance LUT",
    )
    parser.add_argument(
        "--cardback",
        default=None,
        help="Path to a custom cardback image (overrides the default)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directory for cached/cropped images (default: images/ next to script)",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete cached images. If no xml_file given, just clear and exit.",
    )
    return parser


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------
def parse_xml(xml_path):
    """Return (fronts, backs).

    fronts: list of dicts  {id, slots: [int, ...], name}
    backs:  list of dicts  {id, slots: [int, ...], name}
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    def parse_cards(section):
        cards = []
        node = root.find(section)
        if node is None:
            return cards
        for card_el in node.findall("card"):
            cid = card_el.findtext("id").strip()
            slots_text = card_el.findtext("slots").strip()
            slots = [int(s.strip()) for s in slots_text.split(",")]
            name = card_el.findtext("name").strip()
            cards.append({"id": cid, "slots": slots, "name": name})
        return cards

    fronts = parse_cards("fronts")
    backs = parse_cards("backs")

    return fronts, backs


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------
def extension_from_name(name):
    _, ext = os.path.splitext(name)
    return ext  # includes the dot


def download_image(drive_id, name, cache_dir):
    """Download a Google Drive file into cache_dir if not already cached.

    Returns the cached file path, or None if download failed.
    """
    ext = extension_from_name(name)
    cached_path = os.path.join(cache_dir, drive_id + ext)
    if os.path.exists(cached_path):
        return cached_path

    import gdown

    url = f"https://drive.google.com/uc?id={drive_id}"
    print(f"Downloading {name}...")
    try:
        gdown.download(url, cached_path, quiet=True)
    except Exception as e:
        # Clean up partial download
        if os.path.exists(cached_path):
            os.remove(cached_path)
        print(f"  Error downloading {name}: {e}")
        return None
    return cached_path


def download_all(fronts, backs, cache_dir):
    """Download every unique image referenced in the order.

    Returns a dict mapping drive_id -> cached_file_path.
    """
    os.makedirs(cache_dir, exist_ok=True)

    # Collect unique (id, name) pairs – need name for extension
    unique = {}
    for card in fronts:
        unique[card["id"]] = card["name"]
    for card in backs:
        unique[card["id"]] = card["name"]

    id_to_path = {}
    failed = []
    items = list(unique.items())
    need_download = [(did, n) for did, n in items
                     if not os.path.exists(os.path.join(cache_dir, did + extension_from_name(n)))]
    cached_count = len(items) - len(need_download)
    if cached_count > 0:
        print(f"  {cached_count} image(s) already cached, {len(need_download)} to download")

    for i, (drive_id, name) in enumerate(items):
        path = download_image(drive_id, name, cache_dir)
        if path is not None:
            id_to_path[drive_id] = path
        else:
            failed.append((drive_id, name))

        # Delay between actual downloads to avoid Google Drive rate limiting
        if i < len(items) - 1 and (drive_id, name) in need_download:
            delay = 1.5 + random.uniform(0, 1)
            time.sleep(delay)

    # Retry failed downloads once after a longer pause
    if failed:
        print(f"\nRetrying {len(failed)} failed download(s) after pause...")
        time.sleep(10)
        still_failed = []
        for i, (drive_id, name) in enumerate(failed):
            path = download_image(drive_id, name, cache_dir)
            if path is not None:
                id_to_path[drive_id] = path
            else:
                still_failed.append(name)
            if i < len(failed) - 1:
                time.sleep(2 + random.uniform(0, 1.5))
        failed = still_failed

    if failed:
        print(f"\nFailed to download {len(failed)} image(s):")
        for name in failed:
            print(f"  - {name}")
        print("Cards using these images will be skipped.\n")

    return id_to_path


# ---------------------------------------------------------------------------
# Cropping / processing
# ---------------------------------------------------------------------------
def load_vibrance_lut():
    with open(VIBRANCE_CUBE) as f:
        lut_raw = f.read().splitlines()[11:]
    lsize = round(len(lut_raw) ** (1 / 3))
    lut_table = [tuple(float(v) for v in row.split(" ")) for row in lut_raw]
    return ImageFilter.Color3DLUT(lsize, lut_table)


def crop_image(cached_path, drive_id, ext, max_dpi, vibrance_lut, crop_dir):
    """Crop, downscale, apply LUT, and save into crop_dir.

    Returns the crop file path.
    """
    # If no extension, detect format from file header
    if not ext:
        with Image.open(cached_path) as probe:
            fmt = probe.format or "PNG"
        ext = f".{fmt.lower()}"

    crop_path = os.path.join(crop_dir, drive_id + ext)
    if os.path.exists(crop_path):
        return crop_path

    with Image.open(cached_path) as im:
        w, h = im.size
        c = round(0.12 * min(w / 2.72, h / 3.7))
        dpi = c * (1 / 0.12)
        print(f"  Cropping {os.path.basename(cached_path)} – DPI: {dpi:.0f}, bleed: {c}px")
        crop_im = im.crop((c, c, w - c, h - c))

        if dpi > max_dpi:
            scale = max_dpi / dpi
            crop_im = crop_im.resize(
                (int(round(crop_im.size[0] * scale)),
                 int(round(crop_im.size[1] * scale))),
                Image.Resampling.BICUBIC,
            )
            crop_im = crop_im.filter(ImageFilter.UnsharpMask(1, 20, 8))

        if vibrance_lut is not None:
            crop_im = crop_im.filter(vibrance_lut)

        crop_im.save(crop_path, quality=98)

    return crop_path


def crop_all(id_to_cached, max_dpi, vibrance, crop_dir):
    """Process every cached image. Returns dict drive_id -> crop_path."""
    os.makedirs(crop_dir, exist_ok=True)

    vibrance_lut = load_vibrance_lut() if vibrance else None

    print("Cropping images...")
    id_to_crop = {}
    for drive_id, cached_path in id_to_cached.items():
        ext = os.path.splitext(cached_path)[1]
        id_to_crop[drive_id] = crop_image(cached_path, drive_id, ext, max_dpi, vibrance_lut, crop_dir)

    return id_to_crop


# ---------------------------------------------------------------------------
# Slot list
# ---------------------------------------------------------------------------
def build_slot_list(fronts, backs, cardback_path, id_to_crop):
    """Return sorted list of (slot_num, front_crop_path, back_crop_path)."""
    # Map slot -> front crop path (skip cards with failed downloads)
    slot_front = {}
    for card in fronts:
        if card["id"] not in id_to_crop:
            continue
        crop_path = id_to_crop[card["id"]]
        for s in card["slots"]:
            slot_front[s] = crop_path

    # Map slot -> custom back crop path
    slot_back = {}
    for card in backs:
        if card["id"] not in id_to_crop:
            continue
        crop_path = id_to_crop[card["id"]]
        for s in card["slots"]:
            slot_back[s] = crop_path

    default_back = cardback_path

    slots = []
    for slot_num in sorted(slot_front.keys()):
        back_path = slot_back.get(slot_num, default_back)
        slots.append((slot_num, slot_front[slot_num], back_path))

    return slots


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------
def draw_cross(can, x, y, c=6, s=1):
    dash = [s, s]
    can.setLineWidth(s)
    can.setDash(dash)
    can.setStrokeColorRGB(255, 255, 255)
    can.line(x, y - c, x, y + c)
    can.setStrokeColorRGB(0, 0, 0)
    can.line(x - c, y, x + c, y)
    can.setDash(dash, s)
    can.setStrokeColorRGB(255, 255, 255)
    can.line(x - c, y, x + c, y)
    can.setStrokeColorRGB(0, 0, 0)
    can.line(x, y - c, x, y + c)


def generate_pdf(pdf_path, slot_list, page_size, orientation, side="fronts"):
    """Generate a PDF of card images laid out on pages.

    side: 'fronts' or 'backs'
    """
    if orientation == "landscape":
        page_size = (page_size[1], page_size[0])

    pw, ph = page_size
    cols = int(pw // CARD_W)
    rows = int(ph // CARD_H)
    rx = round((pw - (CARD_W * cols)) / 2)
    ry = round((ph - (CARD_H * rows)) / 2)
    cards_per_page = cols * rows

    pages = canvas.Canvas(pdf_path, pagesize=page_size)

    total = len(slot_list)
    for i, (slot_num, front_path, back_path) in enumerate(slot_list):
        img_path = front_path if side == "fronts" else back_path
        j = i % cards_per_page
        row, col = divmod(j, cols)

        if side == "backs":
            col = cols - 1 - col

        if j == 0 and i > 0:
            pages.showPage()

        pages.drawImage(
            img_path,
            col * CARD_W + rx,
            row * CARD_H + ry,
            CARD_W,
            CARD_H,
        )

        # Draw crop marks on last card of a page or last card overall
        if j == cards_per_page - 1 or i == total - 1:
            for cy in range(rows + 1):
                for cx in range(cols + 1):
                    draw_cross(pages, rx + CARD_W * cx, ry + CARD_H * cy)

    pages.save()


# ---------------------------------------------------------------------------
# Clear cache
# ---------------------------------------------------------------------------
def clear_cache(cache_dir, crop_dir):
    """Remove cached and cropped images."""
    for d in [cache_dir, crop_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"Cleared {d}")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(crop_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = build_parser()
    args = parser.parse_args()

    # Resolve cache directories
    image_dir = os.path.join(args.cache_dir, "images") if args.cache_dir else os.path.join(SCRIPT_DIR, "images")
    cache_dir = os.path.join(image_dir, "cache")
    crop_dir = os.path.join(image_dir, "crop")

    # Validate: need at least one of xml_file or --clear-cache
    if not args.xml_file and not args.clear_cache:
        parser.print_usage()
        sys.exit(1)

    # Handle --clear-cache
    if args.clear_cache:
        clear_cache(cache_dir, crop_dir)
        if not args.xml_file:
            print("Cache cleared. No XML file provided; exiting.")
            return

    # --- Normal run ---
    xml_path = args.xml_file
    if not os.path.isfile(xml_path):
        print(f"Error: XML file not found: {xml_path}", file=sys.stderr)
        sys.exit(1)

    # 1. Parse XML
    print(f"Parsing {xml_path}...")
    fronts, backs = parse_xml(xml_path)
    print(f"  {len(fronts)} front card entries, {len(backs)} back card entries")

    # 2. Resolve cardback image
    if args.cardback:
        cardback_path = os.path.abspath(args.cardback)
        if not os.path.isfile(cardback_path):
            print(f"Error: cardback image not found: {cardback_path}", file=sys.stderr)
            sys.exit(1)
    else:
        cardback_path = os.path.join(SCRIPT_DIR, "cardback.jpg")
        if not os.path.isfile(cardback_path):
            print("Error: default cardback.jpg not found in project directory.", file=sys.stderr)
            sys.exit(1)
    print(f"  Using cardback: {cardback_path}")

    # 3. Download images
    print("Downloading images...")
    id_to_cached = download_all(fronts, backs, cache_dir)

    # 4. Crop / process
    id_to_crop = crop_all(id_to_cached, args.dpi, args.vibrance, crop_dir)

    # 4b. Crop cardback through same pipeline
    os.makedirs(crop_dir, exist_ok=True)
    vibrance_lut = load_vibrance_lut() if args.vibrance else None
    ext = os.path.splitext(cardback_path)[1]
    cropped_cardback = crop_image(cardback_path, "cardback", ext, args.dpi, vibrance_lut, crop_dir)

    # 5. Build slot list
    slot_list = build_slot_list(fronts, backs, cropped_cardback, id_to_crop)
    print(f"Total slots: {len(slot_list)}")

    # 6. Generate PDFs
    page_size = PAGE_SIZES[args.paper]
    ps = (page_size[1], page_size[0]) if args.orientation == "landscape" else page_size
    cols = int(ps[0] // CARD_W)
    rows = int(ps[1] // CARD_H)
    cards_per_page = cols * rows
    remainder = len(slot_list) % cards_per_page
    if remainder != 0:
        empty = cards_per_page - remainder
        print(f"Warning: {len(slot_list)} cards don't fill the last page "
              f"({empty} empty slot{'s' if empty != 1 else ''} on a {cards_per_page}-card page).")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(args.output, timestamp)
    os.makedirs(out_dir, exist_ok=True)

    fronts_pdf = os.path.join(out_dir, "fronts.pdf")
    print(f"Generating {fronts_pdf}...")
    generate_pdf(fronts_pdf, slot_list, page_size, args.orientation, side="fronts")

    backs_pdf = os.path.join(out_dir, "backs.pdf")
    print(f"Generating {backs_pdf}...")
    generate_pdf(backs_pdf, slot_list, page_size, args.orientation, side="backs")

    print(f"Done! PDFs saved to {out_dir}/")


if __name__ == "__main__":
    main()
