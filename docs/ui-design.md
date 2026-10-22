# BP-Reviewer UI design direction

## Design intent

BP-Reviewer is a developer-facing analysis workspace, not a marketing dashboard. Its visual hierarchy should help users distinguish the real project structure, the inferred logical blueprint, data/control relationships, and source evidence without adding decorative noise.

## Reference research

- Vercel Geist: https://vercel.com/geist/introduction and https://vercel.com/geist/colors
  - Use neutral surfaces, a restrained semantic accent palette, compact metadata typography, and consistent component states.
- Radix Colors: https://www.radix-ui.com/colors
  - Use distinct but compatible tones for component backgrounds, borders, text, and interaction states; keep contrast legible.
- Mobbin dashboard design analysis: https://mobbin.com/blog/what-good-dashboard-design-looks-like
  - Prioritize information hierarchy, whitespace, and a clear first-screen purpose over adding more panels.
- Dribbble project-management dashboard references: https://dribbble.com/shots/27152549-Project-Management-Dashboard-UI and https://dribbble.com/shots/26616679-Project-Management-Dashboard-Dark-UI
  - Useful as secondary references for compact workspace composition and carefully limited accent colors. Visual effects should not be copied wholesale into a high-density technical tool.

## Palette

| Token | Value | Use |
| --- | --- | --- |
| App background | #F4F6FA | Overall workspace |
| Surface | #FFFFFF | Panels, inputs, nodes |
| Subtle surface | #F8F9FC | Secondary surfaces and hover backgrounds |
| Primary text | #202436 | Headings and important content |
| Secondary text | #747B90 | Metadata and helper text |
| Border | #E1E5EF | Panel and component boundaries |
| Indigo | #5956D6 | Primary actions, selected nodes, active navigation |
| Teal | #168F87 | Data-flow handles and data-related accents |
| Amber | #A66A16 | Warnings and configuration-related accents |
| Red | #B42335 | Errors and destructive feedback |

## Interaction and layout principles

1. Use a compact dark graphite header and neutral workspace panels.
2. Use indigo only for primary actions and selected/active states; use teal for data-flow semantics.
3. Distinguish data, control, import, containment, and unverified relationships by both labels and line styles, not color alone.
4. Keep the directory tree, blueprint canvas, and evidence inspector visually separate but aligned.
5. Use consistent radii, subtle borders, restrained shadows, and short transitions.
6. Preserve visible keyboard focus and respect reduced-motion preferences.
7. Never style inferred relationships as if they were runtime-verified facts.
