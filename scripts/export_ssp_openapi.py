"""Export the mounted SSP product API as a deterministic OpenAPI document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ato_service.main import create_app


async def _readiness_probe() -> dict[str, str]:
    return {}


def export_openapi(output_paths: list[Path]) -> None:
    """Write the current mounted API schema to each requested output path."""

    schema = create_app(readiness_probe=_readiness_probe).openapi()
    rendered = json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    for output_path in output_paths:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "outputs",
        nargs="+",
        type=Path,
        help="OpenAPI JSON output paths",
    )
    args = parser.parse_args()
    export_openapi(args.outputs)


if __name__ == "__main__":
    main()
