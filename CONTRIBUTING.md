# CONTRIBUTING – Development Guide

## Development Environment Setup

Follow the same steps as the README quick start:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Nirav846/myra.git
   cd myra
   ```

2. **Set up Python virtual environment:**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # On Windows
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install frontend dependencies:**
   ```bash
   cd myra_web
   npm install
   cd ..
   ```

5. **Install pre-commit hooks:**
   ```bash
   pre-commit install
   ```

## Code Style

### Python
- **Formatter:** Black
- **Linter:** Flake8
- **Security:** Bandit
- **Type hints:** Recommended for new functions

### TypeScript/JavaScript
- **Formatter:** Prettier
- **Linter:** ESLint
- **Type checking:** TypeScript strict mode

### Pre-Commit Hooks
Pre-commit hooks run automatically on `git commit`:
- Black (Python formatting)
- Flake8 (Python linting)
- MYRA Performance Guard (anti-pattern detection)

To run hooks manually:
```bash
pre-commit run --all-files
```

## CI Pipeline

GitHub Actions runs on every push:
- **Lint:** Flake8, ESLint
- **Type-check:** TypeScript `tsc --noEmit`
- **Security scan:** Bandit
- **Performance guard:** tools/performance_guard.py

## Branch Naming

Use descriptive branch names:
- `feature/feature-name` – New features
- `fix/bug-description` – Bug fixes
- `audit/audit-description` – Security audits
- `refactor/refactor-description` – Code refactoring

## Adding a New Scanner

1. **Add primitive condition (if needed):**
   - Edit `myra_app/scanners/primitives.py`
   - Add your primitive condition function

2. **Create new view:**
   - Create `myra_web/src/views/YourScannerView.tsx`
   - Use existing views as templates (e.g., FVGScanner.tsx)

3. **Register route:**
   - Edit `myra_web/src/App.tsx`
   - Add route in the routes section

4. **Add sidebar entry:**
   - Edit `myra_web/src/App.tsx`
   - Add entry to the `TABS` array

5. **Wire PresetChip (if configurable):**
   - Add preset configuration in `myra_web/src/scannerPresets.ts`
   - Use `PresetChip` component in your view

## Adding a New Indicator

1. **Add calculator:**
   - Create `myra_web/src/core/technical-analysis/indicators/yourIndicator.ts`
   - Implement the calculation logic

2. **Add trace builder:**
   - Create `myra_web/src/core/chart/traces/yourIndicatorTrace.ts`
   - Implement Plotly trace builder

3. **Register in registry:**
   - Edit `myra_web/src/core/chart/registry.ts`
   - Add your indicator to the registry

## Running Audits

### Python
```bash
# Security audit
bandit -r myra_app/

# Lint
flake8 myra_app/

# Performance guard
python tools/performance_guard.py myra_app
```

### TypeScript/JavaScript
```bash
cd myra_web

# Lint
npm run lint

# Type check
npm run type-check

# Format
npm run format
```

## Running Tests

### Backend (Python)
```bash
pytest myra_app/tests/
```

### Frontend (TypeScript)
```bash
cd myra_web
npm test
```

## Issue/PR Templates

When opening issues or pull requests, please include:

### Issue Template
- **Description:** Clear description of the issue
- **Steps to reproduce:** Minimal steps to reproduce
- **Expected behavior:** What should happen
- **Actual behavior:** What actually happens
- **Environment:** OS, Python version, Node version
- **Screenshots:** If applicable

### Pull Request Template
- **Description:** Description of changes
- **Type:** Feature, bug fix, refactor, audit
- **Testing:** How you tested the changes
- **Breaking changes:** Any breaking changes
- **Related issues:** Links to related issues

## Code Review Guidelines

- Keep changes focused and minimal
- Follow existing code style
- Add tests for new features
- Update documentation if needed
- Ensure all CI checks pass

## Performance Guidelines

- Use vectorized operations (Polars/Pandas) instead of loops
- Avoid N+1 queries (batch database operations)
- Use connection pooling for database access
- Implement caching for expensive operations
- Profile performance bottlenecks before optimizing

## Security Guidelines

- Never commit API keys or secrets
- Use environment variables for sensitive data
- Validate all user inputs
- Use parameterized queries for SQL
- Keep dependencies updated
- Run security audits regularly

## Getting Help

- Open an issue for bugs or feature requests
- Check existing documentation first
- Join discussions for questions
- Be respectful and constructive
