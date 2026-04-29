import ast
import os


def get_strategies():
    """
    Safely parses myra_app/myra.py using AST to extract the strategies dictionary
    without triggering complex application imports.
    """
    file_path = "myra_app/myra.py"
    if not os.path.exists(file_path):
        file_path = os.path.join(os.path.dirname(__file__), "..", "myra.py")

    try:
        with open(file_path, "r") as f:
            for node in ast.walk(ast.parse(f.read())):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "strategies"
                ):
                    return ast.literal_eval(node.value)
    except Exception as e:
        print(f"Failed to parse strategies: {e}")
    return {}
