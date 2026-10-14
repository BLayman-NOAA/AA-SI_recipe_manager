# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""API routers of the client server, mounted under ``/api``."""

from aa_recipe_manager.server.models import ErrorResponse

#: Error statuses every router documents, all with the same body.
ERROR_RESPONSES = {
    status: {"model": ErrorResponse} for status in (400, 403, 404, 422, 500)
}
