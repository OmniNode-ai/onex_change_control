# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Valid SQL references — all columns exist in DDL."""

SQL_INSERT = """
INSERT INTO foo (id, name, status)
VALUES ($1, $2, $3)
"""

SQL_SELECT = """
SELECT id, name, status, created_at FROM foo
"""

SQL_UPDATE = """
UPDATE foo SET status = $1
"""
