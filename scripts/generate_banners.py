#!/usr/bin/env python3
"""Generate high-res GitHub README banner and Social Preview image for Oh My Strix."""

import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter


def load_fonts():
    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    ]
    regular_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]

    bold_path = next((p for p in bold_candidates if os.path.exists(p)), None)
    regular_path = next((p for p in regular_candidates if os.path.exists(p)), None)

    return bold_path, regular_path


def read_logo_lines():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logo_path = os.path.join(repo_root, "logo.txt")
    if not os.path.exists(logo_path):
        raise FileNotFoundError(f"logo.txt not found at {logo_path}")

    with open(logo_path, "r", encoding="utf-8") as f:
        return [l.rstrip("\r\n") for l in f.readlines() if l.strip()]


def generate_readme_banner(output_path: str):
    """Generate 1800x520 Retina banner for README.md."""
    width, height = 1800, 520
    bg_color = (13, 17, 23, 255)
    card_bg = (16, 22, 33, 255)

    img = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    margin = 20
    card_box = [(margin, margin), (width - margin, height - margin)]
    draw.rounded_rectangle(card_box, radius=18, fill=card_bg, outline=(48, 54, 61, 255), width=2)

    # Header bar
    draw.rounded_rectangle([(margin, margin), (width - margin, margin + 44)], radius=18, fill=(22, 27, 34, 255))
    draw.rectangle([(margin, margin + 24), (width - margin, margin + 44)], fill=(22, 27, 34, 255))
    draw.line([(margin, margin + 44), (width - margin, margin + 44)], fill=(48, 54, 61, 255), width=1)

    # Buttons
    btn_y = margin + 16
    draw.ellipse([(margin + 20, btn_y), (margin + 34, btn_y + 14)], fill=(239, 68, 68, 255))
    draw.ellipse([(margin + 42, btn_y), (margin + 56, btn_y + 14)], fill=(234, 179, 8, 255))
    draw.ellipse([(margin + 64, btn_y), (margin + 78, btn_y + 14)], fill=(34, 197, 94, 255))

    bold_font, regular_font = load_fonts()
    font_header = ImageFont.truetype(bold_font, 15)
    font_ascii = ImageFont.truetype(bold_font, 25)
    font_sub = ImageFont.truetype(bold_font, 18)
    font_pill = ImageFont.truetype(bold_font, 14)

    # Title
    title_text = "ZanderoDev / oh-my-strix — Autonomous AI Pentesting & CTF Agent"
    tb = draw.textbbox((0, 0), title_text, font=font_header)
    draw.text(((width - (tb[2] - tb[0])) // 2, margin + 14), title_text, fill=(139, 148, 158, 255), font=font_header)

    lines = read_logo_lines()
    line_spacing = 32
    ascii_start_y = margin + 72

    max_w = max(draw.textbbox((0, 0), l, font=font_ascii)[2] - draw.textbbox((0, 0), l, font=font_ascii)[0] for l in lines)
    ascii_start_x = (width - max_w) // 2

    # Glow layer
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for i, line in enumerate(lines):
        glow_draw.text((ascii_start_x, ascii_start_y + i * line_spacing), line, fill=(34, 197, 94, 160), font=font_ascii)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # ASCII text
    green_color = (46, 213, 115, 255)
    for i, line in enumerate(lines):
        draw.text((ascii_start_x, ascii_start_y + i * line_spacing), line, fill=green_color, font=font_ascii)

    # Subtitle
    sub_y = ascii_start_y + len(lines) * line_spacing + 24
    sub_text = "Autonomous AI Agent for Real-World Pentesting, Bug Bounty, and CTF/HTB"
    sb = draw.textbbox((0, 0), sub_text, font=font_sub)
    draw.text(((width - (sb[2] - sb[0])) // 2, sub_y), sub_text, fill=(240, 246, 252, 255), font=font_sub)

    # Badges
    badges = [
        ("⚡ HOLY TRINITY TOOLS", (46, 213, 115)),
        ("🔄 BACKGROUND MULTI-PROCESS", (88, 166, 255)),
        ("🌐 HOST RUNTIME (tun0 / VPN)", (188, 140, 255)),
        ("🎯 PERSISTENT CTF AGENT", (240, 136, 62)),
    ]

    badge_y = sub_y + 44
    badge_spacing = 18
    badge_widths = []
    for text, _ in badges:
        bb = draw.textbbox((0, 0), text, font=font_pill)
        bw = (bb[2] - bb[0]) + 28
        badge_widths.append(bw)

    total_bw = sum(badge_widths) + badge_spacing * (len(badges) - 1)
    bx = (width - total_bw) // 2

    for (text, col), bw in zip(badges, badge_widths):
        draw.rounded_rectangle([(bx, badge_y), (bx + bw, badge_y + 34)], radius=8, fill=(22, 27, 34, 255), outline=col, width=1)
        bb = draw.textbbox((0, 0), text, font=font_pill)
        btw = bb[2] - bb[0]
        draw.text((bx + (bw - btw) // 2, badge_y + 8), text, fill=col, font=font_pill)
        bx += bw + badge_spacing

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, format="PNG", optimize=True)
    print(f"Saved {output_path}")


def generate_social_preview(output_path: str):
    """Generate 1280x640 GitHub Social Preview card."""
    width, height = 1280, 640

    img = Image.new("RGBA", (width, height), (10, 14, 23, 255))
    draw = ImageDraw.Draw(img)

    # Grid
    grid_color = (20, 28, 45, 120)
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # Ambient glow
    glow_center = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_center_draw = ImageDraw.Draw(glow_center)
    center_x, center_y = width // 2, height // 2 - 20
    for r in range(280, 0, -10):
        alpha = int(25 * (1 - r / 280))
        glow_center_draw.ellipse(
            [(center_x - r * 1.6, center_y - r), (center_x + r * 1.6, center_y + r)],
            fill=(16, 185, 129, alpha),
        )
    img = Image.alpha_composite(img, glow_center)
    draw = ImageDraw.Draw(img)

    # Card
    card_margin_x = 48
    card_margin_y = 40
    card_w = width - 2 * card_margin_x
    card_h = height - 2 * card_margin_y
    card_box = [(card_margin_x, card_margin_y), (card_margin_x + card_w, card_margin_y + card_h)]

    draw.rounded_rectangle(card_box, radius=18, fill=(13, 18, 30, 245), outline=(46, 213, 115, 120), width=2)

    header_h = 44
    draw.rounded_rectangle(
        [(card_margin_x, card_margin_y), (card_margin_x + card_w, card_margin_y + header_h)],
        radius=18,
        fill=(19, 26, 42, 255),
    )
    draw.rectangle(
        [(card_margin_x, card_margin_y + 20), (card_margin_x + card_w, card_margin_y + header_h)],
        fill=(19, 26, 42, 255),
    )
    draw.line(
        [(card_margin_x, card_margin_y + header_h), (card_margin_x + card_w, card_margin_y + header_h)],
        fill=(35, 45, 68, 255),
        width=1,
    )

    btn_y = card_margin_y + 16
    draw.ellipse([(card_margin_x + 22, btn_y), (card_margin_x + 34, btn_y + 12)], fill=(239, 68, 68, 255))
    draw.ellipse([(card_margin_x + 42, btn_y), (card_margin_x + 54, btn_y + 12)], fill=(234, 179, 8, 255))
    draw.ellipse([(card_margin_x + 62, btn_y), (card_margin_x + 74, btn_y + 12)], fill=(34, 197, 94, 255))

    bold_font, regular_font = load_fonts()
    font_hdr = ImageFont.truetype(bold_font, 13)
    font_ascii = ImageFont.truetype(bold_font, 20)
    font_title = ImageFont.truetype(bold_font, 22)
    font_sub = ImageFont.truetype(regular_font, 15)
    font_pill = ImageFont.truetype(bold_font, 12)
    font_brand = ImageFont.truetype(bold_font, 13)

    hdr_text = "oh-my-strix ~ ctf & autonomous pentesting agent"
    hb = draw.textbbox((0, 0), hdr_text, font=font_hdr)
    draw.text(((width - (hb[2] - hb[0])) // 2, card_margin_y + 15), hdr_text, fill=(148, 163, 184, 255), font=font_hdr)

    lines = read_logo_lines()
    ascii_start_y = card_margin_y + 70
    line_spacing = 26

    max_ascii_w = max(draw.textbbox((0, 0), l, font=font_ascii)[2] - draw.textbbox((0, 0), l, font=font_ascii)[0] for l in lines)
    ascii_start_x = (width - max_ascii_w) // 2

    # Glow
    glow_ascii = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_ascii_draw = ImageDraw.Draw(glow_ascii)
    for i, line in enumerate(lines):
        glow_ascii_draw.text((ascii_start_x, ascii_start_y + i * line_spacing), line, fill=(46, 213, 115, 180), font=font_ascii)
    glow_ascii = glow_ascii.filter(ImageFilter.GaussianBlur(8))
    img = Image.alpha_composite(img, glow_ascii)
    draw = ImageDraw.Draw(img)

    for i, line in enumerate(lines):
        draw.text((ascii_start_x, ascii_start_y + i * line_spacing), line, fill=(46, 213, 115, 255), font=font_ascii)

    content_y = ascii_start_y + len(lines) * line_spacing + 24
    title_text = "AUTONOMOUS AI PENTESTING & CTF AGENT"
    tb = draw.textbbox((0, 0), title_text, font=font_title)
    draw.text(((width - (tb[2] - tb[0])) // 2, content_y), title_text, fill=(255, 255, 255, 255), font=font_title)

    sub_y = content_y + 36
    sub_text = "HackTheBox • CTF Practice • Red Teaming • Bug Bounty • Web AppSec"
    sb = draw.textbbox((0, 0), sub_text, font=font_sub)
    draw.text(((width - (sb[2] - sb[0])) // 2, sub_y), sub_text, fill=(148, 163, 184, 255), font=font_sub)

    badges = [
        ("⚡ HOLY TRINITY TOOLS", (46, 213, 115)),
        ("🔄 BACKGROUND MULTI-PROCESS", (96, 165, 250)),
        ("🌐 HOST RUNTIME (tun0 / VPN)", (192, 132, 252)),
        ("🎯 PERSISTENT CTF AGENT", (251, 146, 60)),
    ]

    badge_y = sub_y + 40
    badge_spacing = 14
    badge_widths = []
    for text, _ in badges:
        bb = draw.textbbox((0, 0), text, font=font_pill)
        bw = (bb[2] - bb[0]) + 24
        badge_widths.append(bw)

    total_bw = sum(badge_widths) + badge_spacing * (len(badges) - 1)
    bx = (width - total_bw) // 2

    for (text, col), bw in zip(badges, badge_widths):
        draw.rounded_rectangle([(bx, badge_y), (bx + bw, badge_y + 30)], radius=7, fill=(20, 27, 44, 255), outline=col, width=1)
        bb = draw.textbbox((0, 0), text, font=font_pill)
        btw = bb[2] - bb[0]
        draw.text((bx + (bw - btw) // 2, badge_y + 8), text, fill=col, font=font_pill)
        bx += bw + badge_spacing

    foot_y = card_margin_y + card_h - 32
    foot_text = "github.com/ZanderoDev/oh-my-strix"
    fb = draw.textbbox((0, 0), foot_text, font=font_brand)
    draw.text(((width - (fb[2] - fb[0])) // 2, foot_y), foot_text, fill=(100, 116, 139, 255), font=font_brand)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, format="PNG", optimize=True)
    print(f"Saved {output_path}")


if __name__ == "__main__":
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    generate_readme_banner(os.path.join(repo_root, ".github", "banner.png"))
    generate_social_preview(os.path.join(repo_root, ".github", "social-preview.png"))
