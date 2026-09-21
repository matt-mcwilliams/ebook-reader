"""Command-line entry point for the PDF ebook reader."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .dependencies import check_dependencies


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dependency check and then start the GTK application."""

    command_line = list(sys.argv if argv is None else argv)

    if "--check-dependencies" in command_line[1:]:
        result = check_dependencies()
        print(result.format_report())
        return 0 if result.ok else 1

    result = check_dependencies()
    if not result.ok:
        print(result.format_report(), file=sys.stderr)
        return 1

    from .application import EbookReaderApplication

    application = EbookReaderApplication()
    return application.run(command_line)


if __name__ == "__main__":
    raise SystemExit(main())
