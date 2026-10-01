"""Transform saved screenshots into compact model-facing image parts."""

from io import BytesIO
from pathlib import Path

from google.genai import types
from PIL import Image


def compressed_image_part(path: Path, max_side: int, quality: int) -> types.Part | None:
    """Return a bounded JPEG view without changing the saved screenshot artifact."""

    if not path.is_file():
        return None

    with Image.open(path) as source:
        source.load()
        scale = min(1.0, max_side / max(source.width, source.height))
        image: Image.Image = source
        if scale < 1.0:
            new_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
            image = source.resize(new_size, Image.Resampling.BILINEAR)

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
        return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")
