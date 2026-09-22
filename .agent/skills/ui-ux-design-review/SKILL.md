---
name: ui-ux-design-review
description: >
  Use to critique UI/UX designs and screenshots (hierarchy, contrast, spacing, consistency, accessibility); organize design systems into structured libraries (tokens, components, patterns); organize UI/UX features and navigation (information architecture, resolving buried/duplicate actions); and plan or execute clean, outcome-focused implementations in Google Antigravity.
---

# UI/UX Design Review & Design System Organizing

Four core design jobs are housed in this skill. Determine the desired focus before beginning:

## Job 1: Critique a UI/UX Design

Triggered when evaluating a screenshot, mockup, or existing UI screen for usability and polish.

### Process
1. **Grounded Observation**: Describe what is actually visible first (layout, key visual anchors, flows, and states) before making judgments.
2. **Evaluate Core Dimensions**:
   - **Visual Hierarchy**: Is the primary action/content immediately obvious? Does the user's eye naturally land on what matters most?
   - **Typography**: Type scale consistency, font hierarchy, line heights, readability, and contrast against backgrounds.
   - **Color & Contrast**: Adherence to brand 60-30-10 distribution, WCAG 2.1 AA/AAA contrast ratios for text and icons, and consistent semantic colors (success, warning, destructive, informational).
   - **Spacing & Alignment**: Grid consistency (4px/8px rhythm), breathing room, padding uniformity, and alignment across related elements.
   - **Component Consistency**: Do buttons, inputs, pills, cards, and modals share predictable styles, hover states, and behaviors?
   - **Accessibility (a11y)**: Minimum 44x44px touch/click targets, clear focus outlines, high-contrast borders for inputs, and no color-only status indicators.
   - **Content & Microcopy**: Clear, action-oriented button labels, concise helper text, and zero awkward truncation.
   - **Flow & Logic**: Logical progression, absence of dead-ends, clear confirmation states for destructive actions.
3. **Prioritize Concrete Fixes**: Lead with the top 1-3 highest-impact usability improvements. Provide specific, actionable fix directions rather than vague critique.

---

## Job 2: Organize a Design System

Triggered when auditing an application's visual language or unifying scattered styling rules into a coherent token architecture.

### Process
1. **Inventory & Drift Audit**:
   - Scan all stylesheets and inline styles for color values, font sizes, spacing increments, border radiuses, and shadow definitions.
   - Explicitly flag drift (e.g. 4 different shades of blue or conflicting button border radiuses).
2. **Structure Design Tokens**:
   - **Foundations**: Canvas background colors (60%), brand/structural colors (30%), action/CTA accent colors (10%), semantic colors, typography scale, spacing scale, border radius scale, elevation/shadows.
   - **Components**: Standardized variants for Primary CTA buttons, Secondary outline buttons, Ghost buttons, Destructive actions, Form inputs, Badges/Pills, Metric KPI cards, Accordions/Expanders, and Modal Dialogs.
   - **Patterns**: Recurring compositions (e.g., Tab header bars, Bulk action command centers, Filter search bars, Confirmation dialogs).
3. **Deliverable**: A clean design tokens reference and structured CSS/theme layer.

---

## Job 3: Organize UI Features & Information Architecture (IA)

Triggered when an application feels cluttered, features are buried, or actions are duplicated across screens.

### Process
1. **Feature Inventory**: Map every distinct feature, action, and filter across all navigation sections.
2. **Group by User Intent**: Organize around what the user wants to accomplish (e.g., "Find & Select Leads", "Create Outreach Campaign", "Approve & Dispatch", "System Maintenance"), rather than accidental technical boundaries.
3. **Resolve Common IA Flaws**:
   - Merge duplicate or overlapping actions.
   - Elevate high-frequency primary tasks above the fold.
   - Move low-frequency diagnostic or administrative tools into clean progressive-disclosure containers.
   - Standardize placement of primary action buttons (e.g. bottom-left or top-right).
4. **Structured IA Mapping**: Provide a clear hierarchy: Navigation Tab -> Feature Section -> Actions.

---

## Job 4: Execute & Implement in Google Antigravity

Triggered when translating critique and reorganization work into concrete codebase changes.

### Process
1. **Outcome-Focused Implementation Plan**: Define clear goals, design token changes, component refactors, and acceptance criteria in `implementation_plan.md`.
2. **Execute Cleanly**: Refactor stylesheet layers (`ui/theme.py`), reusable components (`ui/components.py`), navigation layout, and tab implementations.
3. **Verify with Rigor**: Run unit test suites, headless UI tests, and visual inspections to ensure zero functional regressions.
