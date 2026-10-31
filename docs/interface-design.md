# Interface design

Daythread should feel calm, familiar, and easy to navigate on iPhone and the web. The content is the focus; controls and technical details support it.

## Shared theme

- Use system typography, clear sentence-case titles, and a small, consistent hierarchy. Page titles name the destination rather than adding a second slogan.
- Use neutral grouped backgrounds, plain content surfaces, subtle separators, and blue for actions and selection. Reserve warning and error colors for states that need attention.
- Keep generous space between sections. Group related controls instead of giving every control its own card.
- Use native navigation and controls on iPhone. On the web, use a quiet desktop sidebar and a stable bottom navigation bar on small screens.
- Keep secondary controls behind labeled menus or disclosures. Show active filters, recording state, errors, and unsaved work clearly even when details are collapsed.
- Support light and dark appearance, keyboard focus, reduced motion, increased contrast, Dynamic Type, and narrow screens. Do not communicate status through color alone.

The native tokens live in `Theme` in `DaythreadApp.swift`; the web tokens and shared presentation rules live in `src/web/src/theme.css`. Web startup HTML, the installed-app manifest, and browser theme color should stay consistent with these tokens.

## Screen priorities

| Screen | Visible first | Available on demand |
| --- | --- | --- |
| Today | Selected date, recording state, daily recap and topics | Filters, meeting shortcuts, evidence, processing details |
| Capture (iPhone) | Start/stop recording, actionable sync state | Processing stages, schedules, devices, upload settings |
| Rewind | Time window and playback action | Recap, source recordings and evidence |
| Diary (web) | Day/week selection and saved entries | Entry details and chat |
| Chat | Conversation and composer | History, search scope, source evidence |
| People & projects | Search, directory and creation action | Naming guidance and edit controls |
| Inbox | Pending items and status selection | Kind/deadline filters, scan controls and diagnostics |
| More (iPhone) | Compact destination rows | Destination-specific controls |
| Settings | Search/categories on web; native category rows on iPhone | Individual preferences and advanced configuration |

Use [Apple's layout guidance](https://developer.apple.com/design/human-interface-guidelines/layout) and [tab bar guidance](https://developer.apple.com/design/human-interface-guidelines/tab-bars) when extending the theme. Keep navigation stable and let the platform adapt its controls to the current OS.
