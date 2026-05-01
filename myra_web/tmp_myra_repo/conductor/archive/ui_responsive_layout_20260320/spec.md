# Specification: Responsive Live Layout & UI_Manager

## 1. Goal
Implement a modern, responsive terminal UI for MYRA using `rich.layout` and `console.screen`. The UI must handle varying terminal widths and provide a structured, high-visibility menu system.

## 2. Requirements
### 2.1 UI_Manager.py
- [ ] Implement `draw_dashboard(lib, breadth_text, forecast=None)` using `rich.layout.Layout`.
- [ ] Layout Zones:
    - **Header**: Fixed size 3, containing Logo and Market Status.
    - **Body**: Ratio 1, containing the Categorized Menu.
    - **Footer**: Fixed size 3, containing the Glossary or Help context.
- [ ] **Menu Table**: A 4-column `Table.grid(expand=True)` inside the Body.
- [ ] **Category Headers**: Solid background colors as follows:
    - `Technicals`: `yellow` on `black`
    - `Institutional`: `magenta` on `black`
    - `Systems/ML`: `cyan` on `black`
    - `Value/Admin`: `green` on `black`
- [ ] **Responsiveness**: `draw_dashboard` must auto-detect `console.width` and adapt the table/layout accordingly.

### 2.2 myra.py Refactor
- [ ] Replace existing manual menu printing with `UI_Manager.draw_dashboard()`.
- [ ] Implement the main loop within a `console.screen()` context to ensure "Live Layout" behavior (full-screen refresh).
- [ ] Restore and categorize all 29 options as per the Institutional Playbook design.

## 3. Success Criteria
- [ ] The UI renders without flickering within the `screen()` context.
- [ ] All 29 options are accessible and correctly categorized.
- [ ] The layout adapts gracefully when terminal size changes.
