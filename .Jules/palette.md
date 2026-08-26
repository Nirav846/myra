## 2023-10-25 - Workspace Buttons Accessibility
**Learning:** Found an accessibility anti-pattern in `SavedWorkspaces.tsx` where a clickable div (for deletion) was nested inside a button (for loading). This is invalid HTML and not accessible to screen readers.
**Action:** Refactored the layout to use a flex container `div` and two separate sibling `<button>` elements, ensuring each has an `aria-label` and `focus-visible` styling.
## 2024-08-26 - Accessible Preset Tabs
**Learning:** In the `ScannerPresetsPanel`, standard `button` tags were being used for preset category filtering. This pattern lacked semantic meaning for screen readers. Using `role="tablist"` on the container and `role="tab"` + `aria-selected` on the buttons clarifies this relationship. Additionally, custom dialogs should have `role="dialog"`, `aria-modal="true"`, and an `aria-labelledby` linking to their heading.
**Action:** When implementing custom modal or tabbed navigation patterns, always include explicit ARIA roles (`dialog`, `tablist`, `tab`, `tabpanel`) and focus ring utility classes (`focus-visible:ring-2`) to guarantee keyboard navigation visibility.
