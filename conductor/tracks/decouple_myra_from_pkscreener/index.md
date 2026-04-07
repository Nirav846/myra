# Track: Decouple MYRA from PKScreener

## Overview
This track aims to make the MYRA stock scanner fully independent of the PKScreener codebase and libraries (PKDevTools, PKNSETools). We will trace all dependencies, copy necessary code into a new `myra_core` package, and update imports.

## Files
- [Specification](./spec.md)
- [Implementation Plan](./plan.md)

## Status
- **Phase 1: Dependency Research**: Completed
- **Phase 2: Extraction & Localization**: Completed
- **Phase 3: Integration & Testing**: Completed
- **Phase 4: Cleanup**: Completed
