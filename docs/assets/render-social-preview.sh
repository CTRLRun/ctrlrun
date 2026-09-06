#!/bin/sh
# Render docs/assets/social-preview.png (1280×640) from docs/assets/social-preview.svg.
#
#     sh docs/assets/render-social-preview.sh
#
# Needs `rsvg-convert` (librsvg): `brew install librsvg` or `apt install librsvg2-bin`. The PNG
# is committed because GitHub's social preview is uploaded by hand from a file; regenerate it
# whenever the SVG changes and upload it again (repository settings → Social preview).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
rsvg-convert --width 1280 --height 640 --format png \
    --output "$here/social-preview.png" "$here/social-preview.svg"
echo "wrote $here/social-preview.png"
