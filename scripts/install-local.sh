#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
user_home=${HOME:?HOME is not set}
data_home=${XDG_DATA_HOME:-"$user_home/.local/share"}
bin_dir="$user_home/.local/bin"
app_dir="$data_home/pdf-ebook-reader"
applications_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/scalable/apps"

if [ ! -d "$project_dir/src/ebook_reader" ]; then
    echo "Could not find the ebook_reader source directory." >&2
    exit 1
fi

install -d "$app_dir" "$bin_dir" "$applications_dir" "$icon_dir"
rm -rf "$app_dir/src"
cp -R "$project_dir/src" "$app_dir/src"
install -Dm644 "$project_dir/pyproject.toml" "$app_dir/pyproject.toml"
install -Dm755 "$project_dir/scripts/ebook-reader-launcher.sh" "$bin_dir/ebook-reader"
install -Dm644 \
    "$project_dir/data/io.github.local.PdfEbookReader.desktop" \
    "$applications_dir/io.github.local.PdfEbookReader.desktop"
install -Dm644 \
    "$project_dir/data/io.github.local.PdfEbookReader.metainfo.xml" \
    "$data_home/metainfo/io.github.local.PdfEbookReader.metainfo.xml"
install -Dm644 \
    "$project_dir/data/icons/hicolor/scalable/apps/io.github.local.PdfEbookReader.svg" \
    "$icon_dir/io.github.local.PdfEbookReader.svg"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

echo "Installed PDF Ebook Reader for the current user."
echo "Launch it with: ebook-reader"
echo "The desktop entry is: $applications_dir/io.github.local.PdfEbookReader.desktop"
