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
import sys
from pathlib import Path
from typing import NamedTuple

# Directories to scan for schema modules
SCHEMA_MODULE_PATHS = [
    "src/onex_change_control/models",
    "src/onex_change_control/enums",
]

# Forbidden module imports for purity.
# These modules can access environment, filesystem, network, or system time.
FORBIDDEN_IMPORTS = frozenset(
    {
        "os",
        "os.environ",
        "dotenv",
        "shutil",
        "tempfile",
        "glob",
        "socket",
        "urllib",
        "urllib.request",
        "urllib.parse",
        "requests",
        "httpx",
        "aiohttp",
        "time",
        "subprocess",
        "multiprocessing",
        "threading",
        "signal",
        "random",
        "secrets",
        "locale",
    }
)

# Forbidden function calls that access environment or current time.
FORBIDDEN_CALLS = frozenset(
    {
        "datetime.now",
        "datetime.today",
        "datetime.utcnow",
        "date.today",
        "os.environ.get",
        "os.getenv",
        "os.getcwd",
        "os.path.expanduser",
        "os.path.expandvars",
        "Path.home",
        "Path.cwd",
    }
)


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

    def visit_Import(self, node: ast.Import) -> None:
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

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
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

    def visit_Call(self, node: ast.Call) -> None:
        """Check function calls for forbidden patterns."""
        call_name = self._get_call_name(node)
        if call_name:
            # Check against forbidden calls (exact match)
            if call_name in FORBIDDEN_CALLS:
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_call",
                        message=f"Forbidden call: '{call_name}' (violates purity)",
                    )
                )
            # Check for patterns like datetime.datetime.now -> datetime.now
            elif "." in call_name:
                parts = call_name.split(".")
                # Try simplified versions (e.g., datetime.datetime.now -> datetime.now)
                # Minimum 3 parts needed: module.class.method
                min_parts_for_simplification = 3
                if len(parts) >= min_parts_for_simplification:
                    simplified = f"{parts[-2]}.{parts[-1]}"
                    if simplified in FORBIDDEN_CALLS:
                        self.violations.append(
                            Violation(
                                file=self.file_path,
                                line=node.lineno,
                                category="forbidden_call",
                                message=(
                                    f"Forbidden call: '{call_name}' (violates purity)"
                                ),
                            )
                        )
            # Check for os.environ access patterns
            if call_name.startswith(("os.environ", "environ.")):
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_call",
                        message=f"Environment access: '{call_name}' (violates purity)",
                    )
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check attribute access for forbidden patterns."""
        attr_chain = self._get_attribute_chain(node)
        # Check for os.environ direct access
        if attr_chain and (
            attr_chain == "os.environ" or attr_chain.startswith("os.environ.")
        ):
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
        """Get the full name of a function call, resolving aliases."""
        if isinstance(node.func, ast.Name):
            # Resolve alias if present
            return self._imported_names.get(node.func.id, node.func.id)
        if isinstance(node.func, ast.Attribute):
            return self._get_attribute_chain(node.func)
        return None

    def _get_attribute_chain(self, node: ast.Attribute) -> str | None:
        """Get the full attribute chain, resolving aliases.

        Example: 'datetime.datetime.now' -> 'datetime.datetime.now'
        """
        parts: list[str] = []
        current: ast.expr = node

        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            # Resolve alias if present
            resolved_name = self._imported_names.get(current.id, current.id)
            parts.append(resolved_name)
            return ".".join(reversed(parts))
        return None


def check_file(file_path: Path) -> list[Violation]:
    """Check a file for both purity and naming convention violations.

    Parses the file once and performs all checks in a single pass.

    Args:
        file_path: Path to the Python file to check

    Returns:
        List of all violations (purity and naming)

    """
    all_violations: list[Violation] = []
    file_name = file_path.name

    # Determine expected prefix based on directory
    if "models" in file_path.parts:
        expected_file_prefix = "model_"
        expected_class_prefix = "Model"
    elif "enums" in file_path.parts:
        expected_file_prefix = "enum_"
        expected_class_prefix = "Enum"
    else:
        return all_violations  # Not a schema module

    # Skip __init__.py
    if file_name == "__init__.py":
        return all_violations

    # Check file naming (doesn't require parsing)
    if not file_name.startswith(expected_file_prefix):
        all_violations.append(
            Violation(
                file=file_path,
                line=1,
                category="naming_file",
                message=f"File '{file_name}' needs prefix '{expected_file_prefix}'",
            )
        )

    # Read and parse file once
    try:
        with file_path.open("r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        all_violations.append(
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=f"Cannot read file ({type(e).__name__}): {e}",
            )
        )
        return all_violations

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        all_violations.append(
            Violation(
                file=file_path,
                line=e.lineno or 1,
                category="syntax_error",
                message=f"Syntax error: {e.msg}",
            )
        )
        return all_violations

    # Check naming conventions (top-level classes only)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            # Flag non-prefixed classes (allow private classes starting with _)
            if not class_name.startswith(
                expected_class_prefix
            ) and not class_name.startswith("_"):
                all_violations.append(
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

    # Check purity (visits entire AST)
    checker = PurityChecker(file_path)
    checker.visit(tree)
    all_violations.extend(checker.violations)

    return all_violations


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

    # Validate that schema directories exist
    missing_dirs: list[str] = []
    for module_path in SCHEMA_MODULE_PATHS:
        full_path = project_root / module_path
        if not full_path.exists():
            missing_dirs.append(module_path)

    if missing_dirs:
        print(f"⚠️  Schema directories not found: {', '.join(missing_dirs)}")  # noqa: T201
        print("   This may indicate a configuration error.")  # noqa: T201
        return 1

    schema_files = find_schema_files(project_root)

    if not schema_files:
        print("⚠️  No schema files found to check")  # noqa: T201
        return 0

    all_violations: list[Violation] = []

    print(f"Checking {len(schema_files)} schema files...")  # noqa: T201
    print()  # noqa: T201

    for file_path in schema_files:
        # Check both purity and naming in a single parse
        violations = check_file(file_path)
        all_violations.extend(violations)

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
