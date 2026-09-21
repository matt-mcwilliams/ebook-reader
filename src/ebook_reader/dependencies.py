"""Runtime checks for the native libraries used by the reader."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class DependencyIssue:
    """One missing or unusable runtime dependency."""

    component: str
    detail: str
    package: str


@dataclass(frozen=True)
class DependencyCheckResult:
    """The result of checking all required runtime dependencies."""

    loaded: tuple[str, ...]
    issues: tuple[DependencyIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def format_report(self) -> str:
        """Return a terminal-friendly status or an actionable error report."""

        if self.ok:
            return "PDF Ebook Reader dependencies are available: " + ", ".join(self.loaded)

        lines = ["PDF Ebook Reader cannot start because required system dependencies are unavailable:"]
        for issue in self.issues:
            lines.append(f"- {issue.component}: {issue.detail}")

        packages = sorted({issue.package for issue in self.issues})
        lines.extend(
            [
                "",
                "On Arch Linux, install the corresponding packages with:",
                f"  sudo pacman -S {' '.join(packages)}",
                "",
                "Then run the application again from the repository root with:",
                "  PYTHONPATH=src python -m ebook_reader",
            ]
        )
        return "\n".join(lines)


def check_dependencies() -> DependencyCheckResult:
    """Load each required namespace and return any actionable failures."""

    issues: list[DependencyIssue] = []
    loaded: list[str] = []

    try:
        import gi
    except ImportError as exc:
        issues.append(
            DependencyIssue(
                "PyGObject",
                f"Python cannot import gi ({exc})",
                "python-gobject",
            )
        )
        gi = None  # type: ignore[assignment]

    if gi is not None:
        for namespace, version, package in (
            ("Gtk", "4.0", "gtk4"),
            ("Poppler", "0.18", "poppler-glib"),
        ):
            try:
                gi.require_version(namespace, version)
                import_module(f"gi.repository.{namespace}")
            except (ImportError, ValueError) as exc:
                issues.append(
                    DependencyIssue(
                        f"{namespace} {version}",
                        f"the GObject namespace could not be loaded ({exc})",
                        package,
                    )
                )
            else:
                loaded.append(f"{namespace} {version}")

    try:
        import cairo  # noqa: F401
    except ImportError as exc:
        issues.append(
            DependencyIssue(
                "Pycairo",
                f"Python cannot import cairo ({exc})",
                "python-cairo",
            )
        )
    else:
        loaded.append("Pycairo")

    return DependencyCheckResult(tuple(loaded), tuple(issues))
