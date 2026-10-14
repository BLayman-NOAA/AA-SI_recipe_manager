# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Write the server's OpenAPI document, from which the client's types are made.

Usage: ``python -m aa_recipe_manager.server.openapi [OUT]``. Without ``OUT``
the document goes to standard output.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.config import ServerConfig


def openapi_document() -> dict[str, Any]:
    """The OpenAPI document of a server with default settings."""
    return create_app(ServerConfig()).openapi()


def openapi_text() -> str:
    """The document as sorted, indented JSON, as it is committed."""
    return json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    text = openapi_text()
    if args:
        with open(args[0], "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
