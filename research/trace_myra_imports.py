
import os
import re
import sys

def get_imports(file_path):
    imports = set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Standard imports: import x, y, z
            matches = re.findall(r'^\s*import\s+([\w\.,\s]+)', content, re.MULTILINE)
            for m in matches:
                for item in m.split(','):
                    imports.add(item.strip().split('.')[0])
            
            # From imports: from x.y import z
            matches = re.findall(r'^\s*from\s+([\w\.]+)\s+import', content, re.MULTILINE)
            for m in matches:
                imports.add(m.strip().split('.')[0])
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
    return imports

def trace_all(start_dir):
    all_imports = set()
    for root, dirs, files in os.walk(start_dir):
        for file in files:
            if file.endswith('.py'):
                path = os.path.join(root, file)
                all_imports.update(get_imports(path))
    return all_imports

if __name__ == "__main__":
    myra_imports = trace_all('myra_app')
    print("All top-level imports found in myra_app:")
    for imp in sorted(myra_imports):
        print(f" - {imp}")
