import sys


def myra_log(current, total, desc="Core Intelligence"):
    percent = (current / total) * 100
    # Only update at significant intervals to keep the CLI clean
    if current % (max(1, total // 100)) == 0 or current == total:
        sys.stdout.write(
            f"\r[MYRA] ⚡ {desc}: {percent:.1f}% Complete | Processing {current}/{total}"
        )
        sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")
        print(f"[*] [MYRA] {desc} Finalized.")
