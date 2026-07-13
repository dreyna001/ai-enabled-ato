"""Pure deterministic extraction library for supported intake formats.

Third-party dependencies (see ``pyproject.toml`` comments):
- ``pypdf``: PDF text-layer decode and page metadata; stdlib cannot parse PDFs.
- ``pypdfium2``: bounded page-to-PNG rendering for governed vision calls; stdlib
  cannot render PDF pages. Diff 2 does not invoke models; rendering is exposed
  only through ``pdf.render_page_png`` for later pipeline stages.
"""

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.limits import resolve_extraction_limits
from ato_service.extraction.router import extract_content
from ato_service.extraction.types import (
    ExtractionContext,
    ExtractionLimits,
    ExtractionOutcome,
    ExtractedSegment,
    VisionPolicy,
)

__all__ = [
    "ExtractionContext",
    "ExtractionError",
    "ExtractionLimits",
    "ExtractionOutcome",
    "ExtractedSegment",
    "VisionPolicy",
    "extract_content",
    "resolve_extraction_limits",
]
