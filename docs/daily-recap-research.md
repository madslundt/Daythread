# Daily recap structure: product research

Reviewed 2026-10-20 using first-party product and support pages. This is design research, not an implementation or a claim that competitors expose the same daily interface. Daythread recommendations were revised after user review to favor adaptive organization without a numeric topic target or cap.

## Recommendation

Treat the daily recap as a short synthesis across recordings, with the recording timeline serving as the detailed index. A day with twelve recording chapters should not automatically have twelve daily headings. The strongest transferable pattern is **brief overview → selected key points → optional source detail**.

Use as many meaningful topics as the recorded day warrants. Merge recurring subjects across recordings, retain outcomes and changes, and leave brief tangents in the recording detail. Revisit earlier topics as new context arrives, including merging and splitting them. Allow useful general topics for smaller related updates. Keep every recording accessible independently of whether its content earns a daily topic. A visible coverage statement should explain that the recap is selective and based on captured material.

## First-party evidence

| Product | Documented behavior | Transfer to Daythread — inference |
|---|---|---|
| **Siri Recap** | Apple's Watch page describes a title, summary and key points for each recap. Its support page describes ambient conversation summaries that act as a brief, high-level memory aid. As of this review, Apple says Siri Recap will become available in beta later in 2026. | Give the daily recap a clear reading hierarchy and an editorial purpose: help someone remember what mattered. Do not turn every transcript subdivision into a heading. This source does not establish a daily grouping algorithm or a numeric topic limit. |
| **Apple notification summaries** | Apple summarizes long or stacked notifications for quick scanning. Priority notifications occupy the top of the stack, while Reduce Interruptions selects notifications that might need immediate attention. | Separate compression from prioritization: merging similar material and choosing what deserves the first screen solve different problems. A recap can be comprehensive in its source access without displaying everything equally. |
| **Omi** | Omi's January 17, 2026 changelog introduces automatic end-of-day recaps, with a default 10 PM local notification configurable in settings. The same release adds folders and intelligent conversation assignment. | A daily synthesis is a distinct product surface from individual conversations. Folder-like context can help recurring subjects cohere, but the changelog does not document how Omi groups daily topics or how many it shows. |
| **Limitless** | The Lifelog interface exposes transcripts by expanding summary headers. Ask AI answers can become daily notifications through “Ask daily.” Users can export summaries, transcripts, or both for selected sections or a day. | Preserve layers: skim summaries, open evidence when necessary, and offer optional focused questions later. This is evidence for progressive disclosure and recurring queries, not proof of a bounded, cross-recording daily summary. |
| **Otter** | Its meeting template separates Overview, Action Items, and Outline. The overview is concise; outline entries navigate to their transcript sources. Meeting-summary emails show highlights with links to all insights and action items. | A short overview and a detailed topic outline serve different reading needs. Keep daily meaning distinct from chapter navigation, and retain source links. These documented examples summarize meetings, not whole days. |
| **Plaud** | Its current Web v4 documentation says automatic summaries read the whole transcript and build a suitable structure. Templates specify alternative structures; its custom-template guidance recommends explicit sections such as decisions, actions and open questions. | Structure should follow the intended reading task. A daily template should differ from a recording template. Avoid mechanically repeating every possible section when the day has no evidence for it. This does not establish Plaud's daily aggregation behavior. |

Sources:

- Siri Recap: [Apple Watch Ultra 4](https://www.apple.com/apple-watch-ultra-4/) and [About Audio Intelligence features](https://support.apple.com/en-us/148354).
- Apple notifications: [Summarize notifications and reduce interruptions](https://support.apple.com/guide/iphone/summarize-notifications-reduce-interruptions-iph1fbe7d2b9/ios).
- Omi: [Official changelog, January 17, 2026](https://feedback.omi.me/en/changelog).
- Limitless: [Interacting with the Pendant](https://help.limitless.ai/en/articles/10546658-interacting-with-the-pendant-search-ask-ai-summaries).
- Otter: [Custom Meeting Type Templates](https://help.otter.ai/hc/en-us/articles/31402572907415-Custom-Meeting-Type-Templates) and [Meeting Summary Overview](https://help.otter.ai/hc/en-us/articles/9156381229079-Meeting-Summary-Overview).
- Plaud: [Summary templates on Plaud Web, v4](https://support.plaud.ai/hc/en-us/articles/63431072621081-Summary-templates-on-Plaud-Web-for-v4-0-x-or-later) and [Custom summary templates](https://support.plaud.ai/hc/en-us/articles/50636094914841-Custom-summary-templates).

**Siri scope distinction:** Apple's announced structure is a compact recap of conversations, with multiple recaps available in its Siri app. The reviewed sources do not establish one synthesized end-of-day digest, deduplication across recaps, a topic cap, or a shipped feature available for hands-on comparison. Daythread's proposed full-day highlights therefore extend the compact-memory-aid idea rather than reproducing a documented Apple daily layout.

## Reading structure after proposal review — design recommendation

The [recommended Daythread design](daily-recap-design.md) separates daily synthesis
and recording detail. It starts without a mandatory introductory overview, avoids
a numeric topic cap and keeps existing follow-up components rather than generating
a duplicate task list. These are product judgments, not competitor specifications.

1. **Daily recap:** a content-driven set of coherent topics, each with one concise summary and expandable source references. Around ten can make sense on a varied day, without making ten a goal or ceiling. Topic granularity can change as the day develops.
2. **Follow-ups:** retain the existing reviewed action and decision components. Keep tentative or unknown states explicit and avoid generating a second competing task list.
3. **Explore recordings:** an explicitly separate route to all recordings and their original topics. Show counts and expand or navigate to detail. Omission from the recap must never mean loss from the archive.

Manage reading effort through concise topic summaries and optional expansion of long prose. The original three-to-five proposal was too restrictive for this use case. No reviewed first-party source establishes an optimal count. Evaluate whether the subjects are well organized and useful rather than treating a lower count as success.

## Grouping and selection rules — inference

- Group by a shared subject and meaningful context: a project, relationship, plan or sustained interest. Repeated labels alone are insufficient; two unrelated conversations about “planning” should not merge.
- Prefer the day's **change**: what was agreed, learned, resolved, advanced or left open. Duration can indicate sustained attention, but a brief important decision may matter more than long small talk.
- Merge repetitions into one theme, retaining references to all supporting recordings. Preserve contradictions or changes over time rather than flattening them into false agreement.
- Use specific labels such as “Kitchen renovation” for substantial subjects, and allow general topics such as “Home and errands” when they provide a useful home for short related updates. Avoid mandatory categories and indiscriminate miscellaneous sections.
- Reconcile the whole selected day after new eligible evidence arrives. Update, merge, split or rename existing topics where their relationships and substance warrant it; do not merely append new sections or merge to reduce the count.
- Keep low-priority material discoverable in recordings. Display synthesized daily topics without hiding them at an arbitrary count, while keeping the complete recording-topic index separate.
- Treat importance as an inference. Preserve evidence and make feedback/correction possible. Missing coverage must be disclosed independently of summary brevity.

## Evaluation of adaptive organization

Review a quiet day, the reported hour-long recording, several recordings of one recurring project, a day with around ten independent useful subjects, brief related everyday updates, a brief consequential decision, contradictory updates, and incomplete capture. Compare successive snapshots as later evidence connects earlier topics or makes a broad topic worth splitting. Check whether independent subjects stay distinct, repetitions collapse, brief important moments survive, and every omitted chapter remains accessible through its recording. Assess reading effort and correction effort; topic count alone does not measure organization quality.
