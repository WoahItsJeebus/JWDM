"""Generate the packaged Windows icon matching JWDM's in-app blue J mark."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate(output: Path) -> None:
    canvas_size = 256
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (4, 4, canvas_size - 4, canvas_size - 4),
        radius=44,
        fill="#2563eb",
    )
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    font_path = windows / "Fonts" / "segoeuib.ttf"
    if not font_path.is_file():
        raise FileNotFoundError(f"Segoe UI Bold is required to generate the icon: {font_path}")
    font = ImageFont.truetype(str(font_path), 168)
    bounds = draw.textbbox((0, 0), "J", font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    position = (
        (canvas_size - width) / 2 - bounds[0],
        (canvas_size - height) / 2 - bounds[1] - 4,
    )
    draw.text(position, "J", font=font, fill="white")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        output,
        format="ICO",
        sizes=((16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)),
    )


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "assets" / "JWDM.ico")
