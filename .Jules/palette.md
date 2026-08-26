## 2023-10-25 - Workspace Buttons Accessibility
**Learning:** Found an accessibility anti-pattern in `SavedWorkspaces.tsx` where a clickable div (for deletion) was nested inside a button (for loading). This is invalid HTML and not accessible to screen readers.
**Action:** Refactored the layout to use a flex container `div` and two separate sibling `<button>` elements, ensuring each has an `aria-label` and `focus-visible` styling.
