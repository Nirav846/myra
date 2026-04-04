import os
import re
import argparse
from pathlib import Path

# Configuration: What to include and ignore
ALLOWED_EXTENSIONS = {'.py', '.json', '.yaml', '.yml', '.ini'}
IGNORED_DIRS = {
    '.git', '__pycache__', 'venv', 'env', '.idea', '.vscode', 'node_modules', 
    'logs', '.ruff_cache', '.pytest_cache', '.mypy_cache', 'data', 'backups'
}
IGNORED_FILES = {
    '.env', 'config.py', 'secrets.json', 'session.txt', 'tokens.json', 
    'codebase_review.md', 'code_backup.py', 'trading_platform.db',
    'trading_platform.db-shm', 'trading_platform.db-wal', 'openalgo logs.txt',
    'test_backup.md', 'test_backup_v2.md'
}

# Regex for redacting sensitive data
# Matches common key-value patterns for secrets in various formats (Python, JSON, YAML)
REDACTION_PATTERN = re.compile(
    r'(?i)(["\"]?(?:api_key|api_secret|secret|token|password|client_id|client_secret|access_key|authorization|bearer|apikey|redirect_uri|mac_address)["\"]?)(\s*[:=]\s*)(["\"])[^"\"]+\3'
)

def redact_content(content):
    """Replaces sensitive string values with [REDACTED]."""
    # Replaces the actual key/secret value but keeps the variable name and separator
    return REDACTION_PATTERN.sub(r'\1\2\3[REDACTED]\3', content)

def is_ignored(path, base_dir):
    """Checks if a file or directory should be ignored."""
    # Check directory parts
    relative_parts = path.relative_to(base_dir).parts
    if any(part in IGNORED_DIRS for part in relative_parts):
        return True
    
    # Check exact file names
    if path.name in IGNORED_FILES:
        return True
    
    # Check extensions
    if path.suffix not in ALLOWED_EXTENSIONS and path.name != 'Dockerfile':
        return True
        
    return False

def generate_backup(source_dir, output_file):
    source_path = Path(source_dir).resolve()
    
    if not source_path.is_dir():
        print(f"Error: Directory '{source_dir}' does not exist.")
        return

    # Ensure output file is ignored if it's in the source directory
    output_path = Path(output_file).resolve()
    
    with open(output_file, 'w', encoding='utf-8') as outfile:
        outfile.write("# Codebase Backup for Review\n")
        outfile.write(f"**Source:** `{source_path.name}`\n")
        outfile.write("**Note:** Sensitive files are excluded and credentials are redacted.\n\n")
        outfile.write("---\n\n")

        processed_files = 0
        
        # Walk through the directory
        for root, dirs, files in os.walk(source_path):
            # Modify dirs in-place to prevent os.walk from entering ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            
            for file in files:
                file_path = Path(root) / file
                
                # Avoid backing up the output file itself
                if file_path.resolve() == output_path:
                    continue
                    
                if is_ignored(file_path, source_path):
                    continue

                try:
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        content = infile.read()
                        
                    redacted_content = redact_content(content)
                    relative_path = file_path.relative_to(source_path)
                    
                    # Determine markdown code block language
                    lang = file_path.suffix.lstrip('.') if file_path.suffix else 'text'
                    if lang == 'bru': lang = 'json'
                    
                    # Write to output file
                    outfile.write(f"### File: `{relative_path}`\n\n")
                    outfile.write(f"```{lang}\n")
                    outfile.write(redacted_content)
                    if not redacted_content.endswith('\n'):
                        outfile.write('\n')
                    outfile.write("```\n\n")
                    
                    processed_files += 1
                    
                except UnicodeDecodeError:
                    # Skip binary files that somehow bypassed the extension check
                    continue
                except Exception as e:
                    print(f"Error processing {file_path}: {e}")

    print(f"✅ Backup complete! Processed {processed_files} files.")
    print(f"📁 Output saved to: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolidate and redact a codebase for review.")
    parser.add_argument("source_dir", nargs="?", default=".", help="The root directory of your project (default: current directory)")
    parser.add_argument("-o", "--output", default="codebase_review.md", help="The output Markdown file (default: codebase_review.md)")
    
    args = parser.parse_args()
    generate_backup(args.source_dir, args.output)
