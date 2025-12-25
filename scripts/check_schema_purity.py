#!/usr/bin/env python3
"""Check schema module purity and naming conventions.

This script enforces:
1. Purity (D-008): No env/fs/network/time usage in schema modules
2. Naming conventions: Model*/model_*, Enum*/enum_*
3. No environment-dependent defaults

Usage:
    poetry run python scripts/check_schema_purity.py

Exit codes:
    0: All checks passed
    1: One or more violations found
"""

import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

# CLI script version
CLI_VERSION = "1.0.0"

# Directories to scan for schema modules
SCHEMA_MODULE_PATHS = [
    "src/onex_change_control/models",
    "src/onex_change_control/enums",
]

# Forbidden module imports for purity
# These modules can access environment, filesystem, network, or system time
FORBIDDEN_IMPORTS = frozenset({
    # Environment access
    "os",
    "os.environ",
    "dotenv",
    # Filesystem access (beyond basic path manipulation)
    "shutil",
    "tempfile",
    "glob",
    # Network access
    "socket",
    "urllib",
    "urllib.request",
    "urllib.parse",
    "requests",
    "httpx",
    "aiohttp",
    # System time (dynamic, non-deterministic)
    "time",
    # Process/system info
    "subprocess",
    "multiprocessing",
    "threading",
    "signal",
    # Random (non-deterministic)
    "random",
    "secrets",
    # Locale (environment-dependent)
    "locale",
})

# Forbidden function calls that access environment or current time
FORBIDDEN_CALLS = frozenset({
    # datetime dynamic access
    "datetime.now",
    "datetime.today",
    "datetime.utcnow",
    "date.today",
    # os environment access
    "os.environ.get",
    "os.getenv",
    "os.getcwd",
    "os.path.expanduser",
    "os.path.expandvars",
    # Path environment access
    "Path.home",
    "Path.cwd",
})

# Allowed datetime usage (parsing, not dynamic time access)
ALLOWED_DATETIME_USAGE = frozenset({
    "date.fromisoformat",
    "datetime.fromisoformat",
    "timedelta",
})


class Violation(NamedTuple):
    """Represents a single purity or naming violation."""

    file: Path
    line: int
    category: str
    message: str


class PurityChecker(ast.NodeVisitor):
    """AST visitor to check for purity violations."""

    def __init__(self, file_path: Path) -> None:
        """Initialize the purity checker.

        Args:
            file_path: Path to the file being checked

        """
        self.file_path = file_path
        self.violations: list[Violation] = []
        self._imported_names: dict[str, str] = {}  # alias -> full module name

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        """Check import statements for forbidden modules."""
        for alias in node.names:
            module_name = alias.name
            stored_name = alias.asname if alias.asname else alias.name
            self._imported_names[stored_name] = module_name

            # Check top-level module
            top_module = module_name.split(".")[0]
            if top_module in FORBIDDEN_IMPORTS or module_name in FORBIDDEN_IMPORTS:
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_import",
                        message=f"Forbidden import: '{module_name}' (violates purity)",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        """Check from-import statements for forbidden modules."""
        if node.module is None:
            self.generic_visit(node)
            return

        module_name = node.module
        top_module = module_name.split(".")[0]

        # Track imported names
        for alias in node.names:
            stored_name = alias.asname if alias.asname else alias.name
            self._imported_names[stored_name] = f"{module_name}.{alias.name}"

        if top_module in FORBIDDEN_IMPORTS or module_name in FORBIDDEN_IMPORTS:
            self.violations.append(
                Violation(
                    file=self.file_path,
                    line=node.lineno,
                    category="forbidden_import",
                    message=f"Forbidden import from: '{module_name}' (violates purity)",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        """Check function calls for forbidden patterns."""
        call_name = self._get_call_name(node)
        if call_name:
            # Check against forbidden calls
            if call_name in FORBIDDEN_CALLS:
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_call",
                        message=f"Forbidden call: '{call_name}' (non-deterministic)",
                    )
                )
            # Check for os.environ access patterns
            elif call_name.startswith("os.environ") or call_name.startswith("environ."):
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_call",
                        message=f"Environment access: '{call_name}' (violates purity)",
                    )
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        """Check attribute access for forbidden patterns."""
        attr_chain = self._get_attribute_chain(node)
        if attr_chain:
            # Check for os.environ direct access
            if attr_chain == "os.environ" or attr_chain.startswith("os.environ."):
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_access",
                        message=f"Environment access: '{attr_chain}' (violates purity)",
                    )
                )
        self.generic_visit(node)

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Get the full name of a function call."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            return self._get_attribute_chain(node.func)
        return None

    def _get_attribute_chain(self, node: ast.Attribute) -> str | None:
        """Get the full attribute chain (e.g., 'datetime.datetime.now')."""
        parts: list[str] = []
        current: ast.expr = node

        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None


def check_naming_conventions(file_path: Path) -> list[Violation]:
    """Check file and class naming conventions.

    Args:
        file_path: Path to the Python file to check

    Returns:
        List of naming convention violations

    """
    violations: list[Violation] = []
    file_name = file_path.name

    # Determine expected prefix based on directory
    if "models" in file_path.parts:
        expected_file_prefix = "model_"
        expected_class_prefix = "Model"
    elif "enums" in file_path.parts:
        expected_file_prefix = "enum_"
        expected_class_prefix = "Enum"
    else:
        return violations  # Not a schema module

    # Skip __init__.py
    if file_name == "__init__.py":
        return violations

    # Check file naming
    if not file_name.startswith(expected_file_prefix):
        violations.append(
            Violation(
                file=file_path,
                line=1,
                category="naming_file",
                message=(
                    f"File name '{file_name}' should start with '{expected_file_prefix}'"
                ),
            )
        )

    # Parse file and check class names
    try:
        with file_path.open("r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except SyntaxError as e:
        violations.append(
            Violation(
                file=file_path,
                line=e.lineno or 1,
                category="syntax_error",
                message=f"Syntax error: {e.msg}",
            )
        )
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            # Check if it's a top-level class (not nested)
            if not class_name.startswith(expected_class_prefix):
                # Allow private classes (starting with _)
                if not class_name.startswith("_"):
                    violations.append(
                        Violation(
                            file=file_path,
                            line=node.lineno,
                            category="naming_class",
                            message=(
                                f"Class '{class_name}' should start with "
                                f"'{expected_class_prefix}'"
                            ),
                        )
                    )

    return violations


def check_purity(file_path: Path) -> list[Violation]:
    """Check a file for purity violations.

    Args:
        file_path: Path to the Python file to check

    Returns:
        List of purity violations

    """
    try:
        with file_path.open("r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return [
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=f"Cannot read file: {e}",
            )
        ]

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        return [
            Violation(
                file=file_path,
                line=e.lineno or 1,
                category="syntax_error",
                message=f"Syntax error: {e.msg}",
            )
        ]

    checker = PurityChecker(file_path)
    checker.visit(tree)
    return checker.violations


def find_schema_files(project_root: Path) -> list[Path]:
    """Find all Python files in schema module directories.

    Args:
        project_root: Path to the project root

    Returns:
        List of Python file paths

    """
    files: list[Path] = []
    for module_path in SCHEMA_MODULE_PATHS:
        full_path = project_root / module_path
        if full_path.exists():
            files.extend(full_path.glob("*.py"))
    return sorted(files)


def print_violation(v: Violation) -> None:
    """Print a violation in a readable format."""
    relative_path = v.file.relative_to(Path.cwd()) if v.file.is_absolute() else v.file
    print(f"  {relative_path}:{v.line}: [{v.category}] {v.message}")  # noqa: T201


def main() -> int:
    """Run purity and naming checks on schema modules.

    Returns:
        Exit code: 0 if all checks pass, 1 if violations found

    """
    project_root = Path(__file__).parent.parent
    schema_files = find_schema_files(project_root)

    if not schema_files:
        print("⚠️  No schema files found to check")  # noqa: T201
        return 0

    all_violations: list[Violation] = []

    print(f"Checking {len(schema_files)} schema files...")  # noqa: T201
    print()  # noqa: T201

    for file_path in schema_files:
        # Check purity
        purity_violations = check_purity(file_path)
        all_violations.extend(purity_violations)

        # Check naming conventions
        naming_violations = check_naming_conventions(file_path)
        all_violations.extend(naming_violations)

    if all_violations:
        print(f"❌ Found {len(all_violations)} violation(s):")  # noqa: T201
        print()  # noqa: T201

        # Group by category
        by_category: dict[str, list[Violation]] = {}
        for v in all_violations:
            by_category.setdefault(v.category, []).append(v)

        for category, violations in sorted(by_category.items()):
            print(f"  {category} ({len(violations)}):")  # noqa: T201
            for v in violations:
                print_violation(v)
            print()  # noqa: T201

        return 1

    print(f"✅ All {len(schema_files)} schema files passed purity and naming checks")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())

