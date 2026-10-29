# PeakShift Design Lock

Confirmed design system specification for the PeakShift factory energy scheduler interface.

## 1. Colour Tokens
- **Backgrounds**:
  - Base: `#0a0e17` (`--bg-base`)
  - Surface/Sidebar: `#111827` (`--bg-surface`)
  - Card/Container: `#1f2937` (`--bg-card`)
  - Hover/Active: `#374151` (`--bg-hover`)
- **Borders**:
  - Default: `#374151` (`--border-default`)
  - Subtle: `#1f2937` (`--border-subtle`)
  - Focus Ring: `#3b82f6` (`--ring-focus`)
- **Text & Contrast** (All text meets WCAG AA >= 4.5:1 against respective backgrounds):
  - Primary text: `#f9fafb` (`--text-primary`)
  - Secondary text: `#9ca3af` (`--text-secondary`)
  - Muted text: `#6b7280` (`--text-muted`)
  - Inverse text: `#111827` (`--text-inverse`)
- **Machine & Gantt States**:
  - Run: `#10b981` (`--state-run`) — emerald green
  - Idle: `#64748b` (`--state-idle`) — slate grey with pattern
  - Off: `#334155` (`--state-off`) — muted dark with dashed border
  - Warmup: `#f59e0b` (`--state-warmup`) — warning amber
- **Tariff & Indicator Overlays**:
  - Peak window highlight: `rgba(239, 68, 68, 0.18)` (`--tariff-peak-bg`)
  - Normal window: `rgba(59, 130, 246, 0.08)` (`--tariff-normal-bg`)
  - Off-peak window: `rgba(16, 185, 129, 0.12)` (`--tariff-offpeak-bg`)
- **Status & Badges**:
  - Success / Target Verified: `#10b981` (bg `#064e3b`, text `#a7f3d0`)
  - Danger / Verification Failed: `#ef4444` (bg `#7f1d1d`, text `#fecaca`)
  - Warning / Target at Risk: `#f59e0b` (bg `#78350f`, text `#fde68a`)
  - Info / Neutral: `#3b82f6` (bg `#1e3a8a`, text `#bfdbfe`)

## 2. Type Scale
- Font family: `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`
- Code/Monospace: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace`
- Scales:
  - Display / Hero: `1.875rem` (30px), line-height 1.2, weight 700
  - Section Header: `1.25rem` (20px), line-height 1.3, weight 600
  - Card Title: `1.0rem` (16px), line-height 1.4, weight 600
  - Body: `0.875rem` (14px), line-height 1.5, weight 400/500
  - Small / Caption / Chart text: `0.75rem` (12px), line-height 1.4, weight 400

## 3. Spacing Scale
- `space-1`: `4px`
- `space-2`: `8px`
- `space-3`: `12px`
- `space-4`: `16px`
- `space-6`: `24px`
- `space-8`: `32px`

## 4. Radius & Shadows
- Radius: `rounded-sm`: 4px, `rounded-md`: 8px, `rounded-lg`: 12px, `rounded-full`: 9999px
- Shadows:
  - `shadow-sm`: `0 1px 2px 0 rgba(0, 0, 0, 0.25)`
  - `shadow-md`: `0 4px 6px -1px rgba(0, 0, 0, 0.35)`
  - `shadow-lg`: `0 10px 15px -3px rgba(0, 0, 0, 0.45)`

## 5. Component Standards
- **Buttons**: Minimum touch target 44px height; clear focus ring (`2px solid var(--ring-focus)`); disabled state with opacity 0.5 and `not-allowed` cursor.
- **Cards**: Surface background `#1f2937`, border `#374151`, subtle shadow, padded 16px.
- **Badges**: Pill-shaped with bold uppercase caption, icon or dot indicator.
- **Tables**: Row hover `#374151`, fixed table headers, aligned numeric values with monospace font.
- **Banners**: `aria-live="polite"` or `assertive`, icon, message, action button.
- **Chart Frames**: Embedded Plotly canvas with responsive height and horizontal scroll container for mobile/tablet.

## 6. Page Specifications
1. **Load Page (`#load`)**:
   - Preset selector pills (`machining`, `textile`, `food_lines`).
   - Sliders for utilization and seed.
   - Drag-and-drop file upload for single JSON/Excel or dual CSV (machines + jobs).
   - Real-time editable tariff table.
   - Inline row-number error display.
   - Primary action button "Run Optimization" (navigates to `#compare`).
2. **Compare Page (`#compare`)**:
   - Status banner with Verifier Badge ("Target verified", "Verification failed", or "Not checked").
   - KPI metrics cards: Total kWh, Energy Cost (₹), Peak kW, Switch-offs, Savings vs B0, Savings vs B1.
   - Winner pill + theoretical lower bound.
   - Two stacked Gantt charts: Baseline B0 vs PeakShift Portfolio.
   - Slot-by-slot kW load profile step chart with peak tariff windows shaded.
3. **Disruption Console (`#disrupt`)**:
   - Event trigger buttons: "Machine Down", "Tariff Shift", "Rush Order", "Job Delay".
   - `now_slot` slider/input.
   - "Target at Risk" alert banner with unplaced jobs & units short if capacity falls short.
   - Before vs After comparison metrics.
   - Interactive Diff Table listing moved/rerouted jobs with reason codes.
   - Re-planned Gantt chart highlighting modified job placements.
4. **Sweep and Peak Limit (`#sweep`)**:
   - Peak-cap slider with real-time Pareto frontier scatter plot (cost vs peak-kW).
   - Load utilization sweep chart (utilization 0.3 to 0.9 vs % savings against B0 and B1).
5. **Reasons and Verifier Report (`#reasons`)**:
   - Verifier audit card: List of all 9 checks with pass/fail badges and detailed rationale.
   - Decision log: Sortable table with reason codes, descriptions, and verified INR savings.
