#!/usr/bin/env python
"""Лист артов: все позы помощницы на светлом и тёмном фоне.

Зачем: артефакты вырезания (светлая кайма по контуру, съеденная одежда,
обрубленный край) на глаз видны только тогда, когда персонаж стоит рядом
на обоих фонах приложения. Скриншоты интерфейса для этого неудобны: там
поза одна, мелкая и только в одной теме.

Картинки вставляются в страницу как data: URL, поэтому лист открывается
двойным щелчком, без сервера. Результат: shots/art-sheet.html (папка shots
в .gitignore, как и остальные снимки для глазной проверки).

Запуск из корня проекта:
    ./venv/Scripts/python.exe tools/art_sheet.py
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CHARACTER = ROOT / "assets" / "character"
OUT = ROOT / "shots" / "art-sheet.html"

# Высота показа: примерно как персонаж стоит в приложении.
HEIGHT = 220


def data_uri(path: Path) -> str:
    """Кадр, уменьшенный для показа, вложенный прямо в страницу."""
    image = Image.open(path).convert("RGBA")
    scale = min(1.0, HEIGHT / image.height)
    image = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def cells(path: Path) -> str:
    """Один кадр на светлой панели и на тёмной."""
    uri = data_uri(path)
    return (
        f'<div class="bg light"><img src="{uri}"></div>'
        f'<div class="bg dark"><img src="{uri}"></div>'
    )


def row(name: str, paths: list[Path]) -> str:
    if not paths:
        return ""
    return (
        f'<div class="row"><div class="name">{name}</div>'
        + "".join(cells(path) for path in paths)
        + "</div>"
    )


def main() -> int:
    rows = []
    for mood in ("idle", "scan", "think", "calm", "panic"):
        frames = [CHARACTER / f"{mood}.png", CHARACTER / f"{mood}-talk-open.png"]
        rows.append(row(mood, [path for path in frames if path.is_file()]))

    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Арты помощницы</title>
<style>
  body {{ background:#22262e; color:#e8ecf4; font:13px sans-serif; margin:8px; }}
  h1 {{ font-size:15px; margin:4px 0 8px; }}
  .row {{ display:flex; align-items:flex-end; gap:6px; margin-bottom:6px; }}
  .name {{ width:56px; }}
  .bg {{ padding:2px; border-radius:4px; }}
  .bg.light {{ background:#e5e9f0; }}
  .bg.dark {{ background:#1b1f28; }}
  .bg img {{ display:block; }}
</style>
<h1>Поза и кадр речи: слева на светлой теме, справа на тёмной.</h1>
{''.join(rows)}
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
