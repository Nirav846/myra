import ast
import os
import sys

# Banned methods that cause performance degradation in quant workflows
BANNED_METHODS = {
    "iterrows": "Use .to_dict('records'), .values, or vectorization.",
    "apply": "Use vectorized Series operations or .map() instead.",
    "strftime": "Heavy string operation. Use .dt accessors if using Pandas.",
}


class PerformanceVisitor(ast.NodeVisitor):
    def __init__(self, filename, lines):
        self.filename = filename
        self.lines = lines
        self.violations = []
        self.in_loop = 0

    def has_noqa(self, lineno, rule):
        if lineno <= 0 or lineno > len(self.lines):
            return False
        line_content = self.lines[lineno - 1]
        # Accept both standard and PG-prefixed noqa comments
        pg_rule_map = {
            "strftime": "PG-STRFTIME",
            "append": "PG-APPEND",
            "chained": "PG-CHAINED",
            "N+1": "PG-NPLUS1",
            "iterrows": "PG-ITERROWS",
            "iloc": "PG-ILOC",
            "apply": "PG-APPLY",
        }
        pg_rule = pg_rule_map.get(rule, rule)
        return (
            f"noqa: {rule}" in line_content
            or f"noqa: {pg_rule}" in line_content
            or "noqa: performance" in line_content
        )

    def visit_For(self, node):
        # Check if this is a retry loop (common false positive for N+1)
        # Only skip if the loop has a clear retry pattern: continue/break in except block
        is_retry = False
        if isinstance(node.target, ast.Name) and node.target.id in [
            "i",
            "j",
            "retry",
            "retries",
        ]:
            # Check for retry pattern: continue/break inside except block
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    for body_node in ast.walk(child):
                        if isinstance(body_node, (ast.Continue, ast.Break)):
                            is_retry = True
                            break
                    if is_retry:
                        break

        if not is_retry:
            self.in_loop += 1

        self.generic_visit(node)

        if not is_retry:
            self.in_loop -= 1

    def visit_While(self, node):
        self.in_loop += 1
        self.generic_visit(node)
        self.in_loop -= 1

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr

            # Check for banned methods
            if attr_name in BANNED_METHODS:
                if not self.has_noqa(node.lineno, attr_name):
                    suggestion = BANNED_METHODS[attr_name]
                    self.violations.append(
                        (
                            node.lineno,
                            "CRITICAL",
                            f"Banned method usage: .{attr_name}(). {suggestion}",
                        )
                    )

            # Check for inefficiencies inside loops
            if self.in_loop > 0:
                if attr_name == "iloc":
                    if not self.has_noqa(node.lineno, "iloc"):
                        self.violations.append(
                            (
                                node.lineno,
                                "WARNING",
                                "High-latency .iloc[] in loop. Use .iat or vectorization.",
                            )
                        )
                if attr_name == "append":
                    if not self.has_noqa(node.lineno, "append"):
                        self.violations.append(
                            (
                                node.lineno,
                                "CRITICAL",
                                "O(N^2) risk: .append() in loop. Use pd.concat or lists.",
                            )
                        )
                if attr_name in ["execute", "get_data", "fetch"]:
                    if not self.has_noqa(node.lineno, "N+1"):
                        self.violations.append(
                            (
                                node.lineno,
                                "LATENCY",
                                "Potential N+1 Query. Move DB/API calls outside the loop.",
                            )
                        )

        self.generic_visit(node)

    def visit_Subscript(self, node):
        # Catch chained indexing: df[mask]['col']
        # Only flag when the outer subscript's value looks like a DataFrame
        if isinstance(node.value, ast.Subscript):
            # Check if the outer variable name suggests a DataFrame
            var_name = None
            if isinstance(node.value.value, ast.Name):
                var_name = node.value.value.id
            elif isinstance(node.value.value, ast.Attribute):
                var_name = node.value.value.attr

            # Only flag if variable name contains df, data, or frame
            if var_name and any(
                keyword in var_name.lower() for keyword in ["df", "data", "frame"]
            ):
                if not self.has_noqa(node.lineno, "chained"):
                    self.violations.append(
                        (
                            node.lineno,
                            "WARNING",
                            "Chained indexing detected: df[x][y]. Use .loc[x, y].",
                        )
                    )
        self.generic_visit(node)


def check_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            lines = content.splitlines(keepends=True)
            tree = ast.parse(content)
            visitor = PerformanceVisitor(filepath, lines)
            visitor.visit(tree)
            return visitor.violations
    except Exception as e:
        return [(0, "ERROR", f"Could not parse file: {e}")]


def main():
    paths = sys.argv[1:] if len(sys.argv) > 1 else ["myra_app", "research", "tools"]
    total_violations = 0
    scanned_files = 0
    exclude_dirs = {"venv", ".git", "__pycache__", "pkscreener_env", ".pytest_cache"}

    print("--- MYRA Performance Guard: Scanning for anti-patterns ---")

    for path in paths:
        if not os.path.exists(path):
            continue

        files = []
        if os.path.isfile(path) and path.endswith(".py"):
            files = [path]
        else:
            for root, dirs, filenames in os.walk(path):
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                for f in filenames:
                    if f.endswith(".py") and f != "performance_guard.py":
                        files.append(os.path.join(root, f))

        for file in files:
            scanned_files += 1
            violations = check_file(file)
            if violations:
                print(f"\nFILE: {file}")
                for line, level, msg in violations:
                    print(f"  Line {line:4} | {level}: {msg}")
                    total_violations += 1

    print("-" * 50)
    print(f"Scan Complete: {scanned_files} files checked.")

    if total_violations > 0:
        print(f"RESULT: Found {total_violations} performance violations.")
        sys.exit(1)
    else:
        print("RESULT: No performance violations detected. Code is quant-ready!")
        sys.exit(0)


if __name__ == "__main__":
    main()
