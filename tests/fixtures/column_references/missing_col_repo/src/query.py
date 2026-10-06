# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Invalid SQL references — 'nonexistent' column does not exist in DDL."""

SQL_SELECT = """
SELECT nonexistent FROM foo
"""

SQL_INSERT = """
INSERT INTO foo (id, name, nonexistent)
VALUES ($1, $2, $3)
"""
