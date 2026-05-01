import os
import re

# Direct path to avoid any folder confusion
ARCHIVE_DIR = r"D:\01screener\Myra\data\Market_Archives"

def standardize_filenames():
    if not os.path.exists(ARCHIVE_DIR):
        print(f"[!] Folder NOT FOUND at: {ARCHIVE_DIR}")
        return

    files = os.listdir(ARCHIVE_DIR)
    print(f"[*] Found {len(files)} total files. Scanning for patterns...")
    
    count = 0
    for filename in files:
        # Pattern 1: Look for 8 digits (DDMMYYYY) anywhere in the name
        match = re.search(r"(\d{2})(\d{2})(\d{4})", filename)
        
        # We only rename if it's NOT already in the correct format
        if match and not filename.startswith("nse_full_20"):
            day, month, year = match.groups()
            new_name = f"nse_full_{year}-{month}-{day}.csv"
            
            old_path = os.path.join(ARCHIVE_DIR, filename)
            new_path = os.path.join(ARCHIVE_DIR, new_name)
            
            try:
                if not os.path.exists(new_path):
                    os.rename(old_path, new_path)
                    print(f"   [RENAME] {filename} -> {new_name}")
                    count += 1
                else:
                    # If target exists, just delete the old duplicate to clean up
                    os.remove(old_path)
                    print(f"   [CLEAN] Removed duplicate: {filename}")
            except Exception as e:
                print(f"   [!] Error on {filename}: {e}")
    
    print(f"\n[MYRA] Standardized {count} files.")

if __name__ == "__main__":
    standardize_filenames()