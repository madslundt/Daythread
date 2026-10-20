# Daily recap: coherent topics that evolve throughout the day

Status: implemented in backend, web and iOS, 20 October 2026. The rules below
describe the intended model behavior; semantic grouping still needs evaluation on
real days. The existing flat Markdown format is retained.

## Recommendation

Let the recorded day determine the topic count. Around ten topics can make sense
on a varied day; fewer or more can also make sense. There is no numeric target,
cap, or automatic overflow threshold. Twelve recording chapters should not
necessarily become twelve daily topics, but twelve genuinely distinct useful
subjects should not be compressed simply to reduce the count.

Keep the recap organized through appropriate topic granularity, deduplication and
concise summaries. Reconsider the whole selected day when new eligible recordings
arrive: update, merge, split, rename or create topics as their substance warrants.
Allow useful general topics for smaller related updates. Keep the complete
recording-topic index separate from daily synthesis.

These are Daythread product recommendations. The [first-party research](daily-recap-research.md)
provides inspiration for concise summaries and optional evidence detail; it does
not establish an optimal number of daily topics.

## Behavior reviewed before implementation

The daily prompt already groups related subjects across recordings and explicitly
permits rewriting, renaming, merging and reorganizing existing sections. The
pipeline synthesizes across all eligible conversation groups selected for the day,
using grounded cached notes rather than simply appending each new recording.
Later merging is a behavior to strengthen and evaluate, not an entirely missing
instruction. Its per-recording example of three subjects becoming three sections
can nevertheless anchor grouping at the wrong level.
See [`Recaps.synthesize`](../src/omi_local/recaps.py).

Both clients previously appended omitted original recording chapters to the same
list as generated daily topics. This could make an organized synthesis look fragmented.
See [`recapItems.ts`](../src/web/src/recapItems.ts),
[`RecapContent.tsx`](../src/web/src/RecapContent.tsx), and
[`RecapContentView.swift`](../src/ios/DaythreadApp/RecapContentView.swift).
Tests now expect deliberate recording-topic browsing instead of including original
chapters in the initial daily recap.

This audit did not inspect the uploaded recording or establish which mechanism
produced its particular twelve rows. Preserve the existing preparation, batched
reduction, source citations, evidence checks and version history.

## Reading structure

| Layer | Presentation |
| --- | --- |
| Daily recap | All synthesized daily topics, each with a concise summary and optional expansion/source inspection |
| All recording topics | A separate **Browse all topics** action with searchable chapters, transcripts and audio |
| Follow-ups | Existing action/decision components retain their own review and state |
| Coverage | Compact cutoff/pending indication with capture gaps, evidence, history and export available on demand |

Use **Daily recap** for the main section. It should cover useful recorded subjects,
not only a handful of exceptional highlights. Do not hide synthesized topics
behind **More from today** merely because a count was reached. Preserve topic
search for long recaps and distinguish original-passage search results from
synthesized topics. Keep day/tag/location scope throughout browsing.

Most topics need one concise paragraph; a brief update may need one sentence and
a substantial discussion may need two paragraphs. Preserve useful names, dates,
conditions and uncertainty. Expand long prose rather than imposing a fixed total
word budget or writing repetitive short and long versions. Do not add a mandatory
overview that repeats the same content.

## Choosing topic granularity

Create a specific topic when it has enough independent substance that the owner
would look for it later: **Kitchen renovation**, **Weekend trip**, **Product
launch**. Pricing, copy and release timing can belong to one launch topic. Two
unrelated projects should stay distinct even if both discuss pricing. Shared
vocabulary and recording boundaries do not establish a shared subject.

Allow general topics such as **Home and errands**, **Family plans** or **Work
updates** when several short items naturally belong together and none needs its
own section. These can stand alone; a nested hierarchy is unnecessary. Keep
independent arrangements distinguishable with clear sentences and attribution.

A broad topic is useful when it improves findability and gives smaller updates a
natural home. It becomes too broad when the summary reads as an unrelated list,
important subjects become hard to find, or several subjects need substantial
independent explanations. Split those subjects out when useful. For example, a
brief renovation update may start under **Home and errands**, then become
**Kitchen renovation** after a detailed budget discussion. Remove moved details
from the general topic so each fact appears once.

Do not prescribe permanent Work/Personal/Health categories, create empty category
placeholders, or use **Other** as a dumping ground. Conversely, do not forbid a
helpful broad topic just because each sentence could have its own specific title.

## Reconciliation throughout the day

After new evidence becomes eligible under the existing pause/upload rules and
generation batching, reassess the whole selected day:

1. **Update** an existing topic with new details, consolidating repeated facts.
2. **Merge** existing topics when their content or later evidence establishes a
   shared subject or a clearer natural grouping. This can merge topics created
   hours earlier. Reducing the topic count is not itself a reason to merge.
3. **Split** a growing subject from a broad topic when its substance or independent
   usefulness warrants a separate heading.
4. **Create** a topic for a distinct useful subject that does not fit an existing
   specific or general topic.
5. **Rename/reorder** when this makes the day clearer. Preserve ordering where
   practical, but do not preserve a poor early structure at the expense of clarity.

Morning topics **Travel options** and **Hotel booking** can become **Weekend trip**
when later context establishes that they concern the same trip. If they concern
different trips, keep them separate. A Tuesday launch plan and its later change
to Thursday can share **Product launch**, with the change and any unresolved
conflict made explicit.

Use grounded notes for all selected conversations and preserve references to
original sources. Do not repeatedly summarize only the previous recap plus new
material: that can accumulate omissions and interpretation drift. A previous
topic map may help continuity as an organizational hint, never as new evidence.
Reconsider relationships among earlier topics, not only matches to the new input.

Retain all supporting source references through merges and splits. A later
mention does not by itself establish agreement, cancellation or completion.
Corrections and deletion must invalidate dependent content through existing
checks. Do not let changed row positions redirect an open topic or action filter
to another subject. If explicit topic IDs are introduced, preserve continuing
identity and record replacements after merges/splits; translated titles must not
determine identity.

## Selection rules

Include lasting useful substance: plans, meaningful personal moments, useful
knowledge, consequential changes, decisions, explicit things to remember and
concrete unresolved concerns. Ordinary errands and personal interests can matter
as much as work. Do not restrict the recap to major outcomes.

Omit incidental chatter without later usefulness. Preserve explicit requests to
remember something even when brief. Duration, repetition and button markers alone
do not establish importance. Keep useful candidate details across input batches
before final day-level organization; do not discard them with per-batch quotas.
Keep suggestions tentative and avoid inventing significance or obligations.

Create only topics with supported substance. Do not add topics to approach ten,
or drop a meaningful topic because there are already ten. State each useful
detail once within the recap.

## Illustrative outcomes

- Twelve recording chapters about a launch, a trip, family plans and tangents may
  become four daily topics because of their relationships and usefulness.
- Ten independent useful subjects can produce ten topics. Twelve can produce twelve.
- Eight fragmented morning topics may become six after later context connects
  them, then grow to nine when new subjects arise. Count can decrease or increase.
- A quiet day can have one general **Family plans** topic covering several small
  arrangements; a growing subject can later be split out.

These are examples, not an analysis of the uploaded recording.

## Implementation

Both clients separate the generated recap from original-topic browsing, reusing
existing search, translations, source validation and playback. An empty selective
recap still offers recording access without showing raw chapters as recap topics.
This needs no additional inference or saved Markdown format change.

The daily prompt replaces the per-recording subject-count example with day-level
granularity and reconciliation rules, including general-topic and merge/split
examples. Whole-day synthesis, current batching and conversation preparation are
preserved. `DAY_PROMPT_VERSION` is bumped to refresh daily output while leaving
recording chapters intact.

Flat Markdown is retained. A numeric cap, ranking pipeline or mandatory
structured-output migration is unnecessary. If continuity through merges/splits
needs stronger guarantees, add versioned topic IDs and replacement mappings as a
focused follow-up, updating translation mapping, history/export, evidence and
both clients together. Filtered daily recaps use only selected original sources
and keep the filter in the topic browser. Meeting recaps and recording chapters
retain their separate reading purposes.

## Acceptance checks

- Twelve chapters do not automatically yield twelve daily topics; twelve distinct
  useful subjects are also not forcibly reduced or hidden.
- Around ten coherent topics remain visible with no fixed overflow cutoff. One
  useful topic stays one; an uninformative day can leave the recap empty.
- Repeated discussions consolidate while unrelated projects remain separate.
- Later evidence can merge earlier topics and split a growing subject from a
  broad topic, preserving all useful details and source references once.
- General topics organize brief related updates without becoming catch-all lists.
- Necessary regrouping preserves topic/evidence focus; avoid needless layout churn.
- Late useful details and short reminders survive reduction. Changed plans and
  contradictions retain qualifications. Pending work and gaps remain disclosed.
- Corrected, expired or deleted evidence cannot silently return through recap
  details, original browsing or cached translations.
- Web/iOS agree on membership and order; translations preserve source identity.
  Filtered browsing does not expose excluded sources.

Evaluate quiet, personal, meeting-heavy and mixed days, including successive
snapshots of each evolving day. Assess fragmentation, inappropriate merging,
duplication, findability, unnecessary layout changes and retention of useful
details. Topic count is an observation, not a pass/fail metric. Prompt-string
assertions alone cannot establish good organization.
