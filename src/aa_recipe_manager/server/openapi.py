# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Write the server's OpenAPI document, from which the client's types are made.

Usage: ``python -m aa_recipe_manager.server.openapi [OUT]``. Without ``OUT``
the document goes to standard output.
"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from typing import Any

from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.config import ServerConfig


#: Response descriptions FastAPI takes from Python's HTTP status phrases, for
#: the codes Python 3.13 renamed to RFC 9110 wording ("Content Too Large",
#: "Range Not Satisfiable", "Unprocessable Content"). Pinned to the earlier
#: phrases so the committed document does not depend on the Python version.
_PINNED_PHRASES = {
    413: "Request Entity Too Large",
    416: "Requested Range Not Satisfiable",
    422: "Unprocessable Entity",
}


def openapi_document() -> dict[str, Any]:
    """The OpenAPI document of a server with default settings."""
    document = create_app(ServerConfig()).openapi()
    for operations in document.get("paths", {}).values():
        for operation in operations.values():
            for code, response in operation.get("responses", {}).items():
                if not code.isdigit() or int(code) not in _PINNED_PHRASES:
                    continue
                if response.get("description") == HTTPStatus(int(code)).phrase:
                    response["description"] = _PINNED_PHRASES[int(code)]
    return document


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
