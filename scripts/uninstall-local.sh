#!/bin/sh
set -eu

user_home=${HOME:?HOME is not set}
data_home=${XDG_DATA_HOME:-"$user_home/.local/share"}
bin_dir="$user_home/.local/bin"
app_dir="$data_home/pdf-ebook-reader"
applications_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/scalable/apps"

rm -f "$bin_dir/ebook-reader"
rm -f "$applications_dir/io.github.local.PdfEbookReader.desktop"
rm -f "$icon_dir/io.github.local.PdfEbookReader.svg"
rm -f "$data_home/metainfo/io.github.local.PdfEbookReader.metainfo.xml"
rm -rf "$app_dir"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

echo "Removed the PDF Ebook Reader application files."
echo "Saved reading state was left untouched."
