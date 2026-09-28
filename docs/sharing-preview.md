# Sharing this project

The public page declares an opaque Obscur sharing image in its initial HTML.
It is an authored summary of retained evidence, not an application screenshot.
The image is 1280 by 640 pixels and remains below 1 MB. Inter Regular, Semibold,
Bold and genuine Italic glyphs are rasterized from explicitly verified files.
No font file or remote font request is part of the image.

Rebuild with the existing optional figure dependencies, including Pillow and
fontTools, and an existing directory of official static Inter faces:

```sh
python tools/render_share_preview.py --font-dir /path/to/Inter/extras/ttf
python tools/render_share_preview.py --check
```

The adapter reads the maintained source and palette identified in
[the card specification](sharing-card.json). Its [receipt](sharing-card-receipt.json)
records the content, source identity, font faces and PNG bytes. It does not run
an experiment. The original report graphics and source evidence remain unchanged.

A GitHub repository social preview is a separate repository setting. Updating
this page does not change that setting or an existing LinkedIn Featured item's
saved image, title or description. Check the actual item before replacing it.
One opaque Obscur image is intentional. No automatic appearance switching is
claimed for a LinkedIn card. The project's own appearance controls remain separate.
