# Specification: Decouple MYRA from PKScreener

## Goal
Remove the dependency on the `pkscreener/` root folder and the `PKDevTools`, `PKNSETools`, and `PKBrokers` libraries by localizing the used code into a `myra_core` package.

## Requirements
1.  **Independent Package**: Create a `myra_core/` folder that contains all the logic currently imported from `PKDevTools`, `PKNSETools`, and `PKBrokers`.
2.  **Import Refactoring**: Update all files in `myra_app/` to import from `myra_core` instead of the old locations.
3.  **Dependency Removal**: Ensure `myra_app` runs correctly even if the `pkscreener/` folder is deleted and the `PK*` libraries are uninstalled.
4.  **Zero Regression**: Maintain all existing functionality of the stock scanner.

## Target Modules to Localize
Based on initial research:
- `PKDevTools.classes.PKDateUtilities`
- `PKNSETools.classes.PKScreenerDataSync` (if used)
- Any other modules found during recursive import tracing.

## Out of Scope
- Refactoring `myra_app` internal logic unless necessary for the decouple.
- Updating `legacy_pkscreener`.
