"""Protocol PDF difference reporting.

The package is intentionally small and local-first: it extracts selectable text
from two PDF files, segments the text by protocol-style headings, matches the
old and new sections, and writes human-readable reports that point reviewers to
the likely chapter/section updates.
"""

__all__ = ["__version__"]

__version__ = "1.4.0"
