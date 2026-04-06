import ast
import os
import sys

# Banned methods that cause performance degradation in quant workflows
BANNED_METHODS = {"iterrows", "apply", "strftime"}

class PerformanceVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.violations = []
        self.in_loop = 0

    def visit_For(self, node):
        self.in_loop += 1
        self.generic_visit(node)
        self.in_loop -= 1

    def visit_While(self, node):
        self.in_loop += 1
        self.generic_visit(node)
        self.in_loop -= 1

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in BANNED_METHODS:
                self.violations.append((node.lineno, f"🚫 Banned method usage: .{attr_name}()"))
            
            if self.in_loop > 0:
                if attr_name == "iloc":
                    self.violations.append((node.lineno, "⚠️ High-latency operation in loop: .iloc[] (Use .iat or vectorization)"))
                if attr_name == "append":
                    # DataFrame.append is O(N^2) in loops and deprecated in newer Pandas
                    self.violations.append((node.lineno, "🔥 O(N^2) risk: .append() call inside loop (Use pd.concat)"))

        self.generic_visit(node)

    def visit_Subscript(self, node):
        # Catch chained indexing: df[mask]['col']
        if isinstance(node.value, ast.Subscript):
             self.violations.append((node.lineno, "🔗 Chained indexing detected: df[x][y] (Use .loc[x, y] for performance/safety)"))
        self.generic_visit(node)

def check_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            tree = ast.parse(content)
            visitor = PerformanceVisitor(filepath)
            visitor.visit(tree)
            return visitor.violations
    except Exception as e:
        return [(0, f"❌ Error parsing file: {e}")]

def main():
    """
    Performance Guard for MYRA.
    Scans files for anti-patterns that degrade backtesting/scanning performance.
    """
    # Default to myra_app if no path provided
    paths = sys.argv[1:] if len(sys.argv) > 1 else ["myra_app", "research", "tools"]
    total_violations = 0
    scanned_files = 0
    
    print("🚀 MYRA Performance Guard: Scanning for anti-patterns...")
    
    for path in paths:
        if not os.path.exists(path):
            continue
            
        if os.path.isfile(path):
            files = [path]
        else:
            files = []
            for root, _, filenames in os.walk(path):
                # Skip virtual envs and caches
                if any(x in root for x in ["pkscreener_env", "__pycache__", ".git"]):
                    continue
                for f in filenames:
                    if f.endswith(".py"):
                        files.append(os.path.join(root, f))
        
        for file in files:
            scanned_files += 1
            violations = check_file(file)
            if violations:
                print(f"\n--- {file} ---")
                for line, msg in violations:
                    print(f"  [Line {line}] {msg}")
                    total_violations += 1
    
    print(f"\n{'='*50}")
    print(f"Scan Complete: {scanned_files} files checked.")
    if total_violations > 0:
        print(f"❌ Found {total_violations} performance violations.")
        # Exit with error code to integrate with pre-commit/CI
        sys.exit(1)
    else:
        print("✨ No performance violations detected. Code is quant-ready!")
        sys.exit(0)

if __name__ == "__main__":
    main()
