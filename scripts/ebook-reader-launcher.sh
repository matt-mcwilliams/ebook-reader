#!/bin/sh
set -eu

user_home=${HOME:?HOME is not set}
data_home=${XDG_DATA_HOME:-"$user_home/.local/share"}
app_root="$data_home/pdf-ebook-reader"

if [ ! -d "$app_root/src/ebook_reader" ]; then
    echo "PDF Ebook Reader is not installed for this user." >&2
    echo "Run scripts/install-local.sh from the project checkout." >&2
    exit 1
fi

if [ -n "${PYTHONPATH-}" ]; then
    PYTHONPATH="$app_root/src:$PYTHONPATH"
else
    PYTHONPATH="$app_root/src"
fi
export PYTHONPATH
exec python3 -m ebook_reader "$@"
