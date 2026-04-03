# Strict Guardrails (Zero-Breakage Mandate)
- **NEVER** bypass `librarian.py` to write data directly to disk.
- **NEVER** overwrite an entire file unless explicitly instructed. Provide precise modifications.
- **NEVER** mix pure UI logic in `myra.py` with data-fetching or screening logic.

# Anti-Breaking Protocols
- **Caller Verification:** Check upstream callers before changing a function's logic.
- **Signature Preservation:** NEVER alter an existing function signature (arguments/returns) without updating dependent calls.
- **Type Safety:** Maintain strict Python type hinting (`-> pd.DataFrame`, etc.) for DuckDB schema compatibility.
