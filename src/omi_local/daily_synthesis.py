"""Plan a day's subjects before writing scoped, source-cited topic summaries."""

import asyncio
import json
import re

from .inference import InferenceError, summary_language_instruction, validate_citations
from .summary_filter import NO_CONTENT, filter_summary_sources

BUDGET = 20000
PLAN = (
    "Organize useful recorded information into coherent daily topics. This is ONLY a topic "
    "planning task, not summary writing. Supplied context labels are provisional; read the "
    "individual facts and regroup them. Use a project, plan, goal or workflow as the topic, "
    "not one topic per technical detail. Latency, file locks, fetching, synchronization and "
    "scaling in the same ingestion workflow belong together. Separate substantive independent "
    "projects, hiring, a catalog launch and a different application's beta feedback. Never "
    "attach other projects to a named project's heading. Smaller related updates may share a "
    "useful general topic. Shared vocabulary or being in the same recording is not enough. "
    "Select lasting useful outcomes, changes, follow-ups, plans, unresolved concerns and "
    "durable information; include explicit requests to remember ordinary errands and personal "
    "plans. Omit chatter, opaque fragments and incidental background. "
    "There is no target, minimum or maximum topic count. Reconsider all earlier material: "
    "merge connected subjects, split growing independent subjects and remove duplication. "
    "Return only JSON: {\"topics\":[{\"title\":\"short specific subject\","
    "\"scope\":\"what belongs in this topic, excluding other subjects\","
    "\"items\":[\"n1\",\"n2\"]}]}. Use only supplied item IDs. Each topic needs supporting "
    "items; an item may support different scopes if it contains several subjects. Empty "
    "topics means nothing useful qualifies. Do not return summaries, citations or Markdown."
)

CHECK = (
    "Decide whether ONE proposed topic mixes genuinely independent subjects. First make a "
    "binary decision: split_required is true ONLY for unrelated projects or independent "
    "personal arrangements. It is false for different problems, owners or technical details "
    "of the SAME project or workflow. Latency, locked files, fetching, synchronization and "
    "scaling in one ingestion workflow must stay together. Recruitment, a catalog launch "
    "and a different application's beta feedback are independent subjects. The suggested "
    "scope is only a hypothesis; use the facts to decide, not inherited parent labels. "
    "Preserve EVERY supplied item ID. There is no numeric topic target. Return only JSON: "
    "{\"split_required\":false,\"topics\":[{\"title\":\"specific subject\","
    "\"scope\":\"what belongs here\",\"items\":[\"n1\",\"n2\"]}]}. "
    "If split_required is false, return exactly one topic containing all items. If true, "
    "return the independently useful subjects separately. No summaries or citations."
)


def note_items(sources):
    allowed = {source['id'] for source in sources} | {
        key for source in sources for key in source.get('citation_source_ids', [])
    }
    items = []
    for source in sources:
        heading, buffer = '', []

        def flush():
            if not buffer:
                return
            text = ' '.join(buffer).strip()
            buffer.clear()
            label = re.match(r'^\*\*([^*]+)\*\*\s*:', text)
            context = label[1] if label else heading
            refs = [key for key in sorted(allowed) if key.lower() in text.lower()]
            refs = refs or [source['id']]
            # Bound unusually long paragraphs without discarding their tail or provenance.
            while text:
                end = len(text) if len(text) <= 6000 else text.rfind(' ', 1, 6000)
                if end <= 0:
                    end = 6000
                piece, text = text[:end], text[end:].lstrip()
                items.append({'id': f'n{len(items) + 1}', 'context': context,
                              'parent_context': heading, 'text': piece, 'sources': refs})

        for line in (source.get('text') or '').splitlines():
            title = re.match(r'^#{1,6}\s+(.+)', line)
            bullet = re.match(r'^\s*(?:[-*•]|\d+\.)\s+(.+)', line)
            if title:
                flush()
                heading = title[1]
            elif bullet:
                flush()
                buffer.append(bullet[1])
            elif not line.strip():
                flush()
            else:
                buffer.append(line.strip())
        flush()
    return items


def planning_item(item):
    text = item['text']
    for key in item['sources']:
        text = re.sub(re.escape(key), '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*[,\s]*\]', '', text)
    return {'id': item['id'], 'context': item['context'],
            'parent_context': item['parent_context'], 'text': text}


def batches(rows):
    result, current, size = [], [], 0
    for row in rows:
        length = len(json.dumps(row, ensure_ascii=False))
        if current and size + length > BUDGET:
            result.append(current)
            current, size = [], 0
        current.append(row)
        size += length
    if current:
        result.append(current)
    return result


def parse_plan(answer, known, required=None):
    try:
        text = answer.strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
        topics = json.loads(text)['topics']
        if not isinstance(topics, list):
            raise ValueError()
        titles, selected, result = set(), set(), []
        for topic in topics:
            title, scope, ids = topic['title'], topic['scope'], topic['items']
            if isinstance(title, str):
                title = title.strip()
            if (not isinstance(title, str) or not title.strip() or '\n' in title
                    or len(title) > 300 or title.lstrip().startswith('#')
                    or title.casefold() in titles or not isinstance(scope, str)
                    or not scope.strip() or len(scope) > 2000
                    or not isinstance(ids, list) or not ids
                    or any(not isinstance(key, str) or key not in known for key in ids)):
                raise ValueError()
            titles.add(title.casefold())
            selected.update(ids)
            result.append({'title': title.strip(), 'scope': scope.strip(),
                           'items': list(dict.fromkeys(ids))})
        if required is not None and selected != required:
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError):
        raise InferenceError('Invalid LLM response') from None


async def summarize_daily(inference, sources, *, original_sources=None):
    original = sources
    sources, filtering = filter_summary_sources(sources)
    inference.record_summary_filter(filtering, original)
    items = note_items(sources)
    trace = {'model_calls': 0, 'reduction_used': False, 'input_filtering': filtering,
             'final_prompt_source_ids': [], 'organization': 'planned_scoped_topics',
             'writing_evidence': 'original_transcripts' if original_sources is not None
             else 'prepared_notes'}
    if not items:
        return NO_CONTENT, trace
    lookup = {item['id']: item for item in items}
    language = summary_language_instruction(inference.settings.summary_language)

    async def plan(rows, required=None, single_topic=False):
        task = (CHECK if single_topic else PLAN) + language
        if required is not None and not single_topic:
            task += (' Reconcile these provisional topics across all batches. Preserve EVERY '
                     'selected item ID, while merging related workflows and splitting unrelated '
                     'subjects. Titles and scopes are navigation hints, not additional facts.')
        payload = rows
        if required is not None:
            facts = [planning_item(item) for item in items if item['id'] in required]
            if single_topic:
                for fact in facts:
                    fact.pop('parent_context', None)
                reviewed = {'suggested_scope': rows[0]['scope'], 'supporting_items': facts}
            else:
                reviewed = {'proposed_topics': rows, 'supporting_items': facts}
            if len(json.dumps(reviewed, ensure_ascii=False)) <= 24000:
                payload = reviewed
            if not single_topic:
                task += (' Audit the proposed grouping, not just its wording. Merge separate '
                     'latency, locked-file, fetching-status, synchronization and scaling '
                     'topics when the facts establish the same workflow. Keep genuinely '
                     'independent projects findable. Do not preserve a draft topic count.')
        for attempt in range(2):
            trace['model_calls'] += 1
            answer = await inference.for_task('summary').generate(
                task, json.dumps(payload, ensure_ascii=False),
            )
            try:
                if single_topic:
                    raw = answer.strip()
                    if raw.startswith('```'):
                        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
                    try:
                        decision = json.loads(raw)['split_required']
                    except (ValueError, KeyError, TypeError):
                        raise InferenceError('Invalid LLM response') from None
                    result = parse_plan(answer, required, required)
                    if not isinstance(decision, bool) or (not decision and len(result) != 1):
                        raise InferenceError('Invalid LLM response')
                    return result if decision else rows
                return parse_plan(answer, required if required is not None else
                                  {row["id"] for row in rows}, required)
            except InferenceError:
                if attempt:
                    raise
                task += ' The previous plan was invalid. Follow the JSON schema and known item IDs.'

    groups = batches([planning_item(item) for item in items])
    topics = []
    for group in groups:
        topics.extend(await plan(group))
    if not topics:
        return NO_CONTENT, trace
    trace['reduction_used'] = len(groups) > 1
    for _ in range(8):
        groups = batches(topics)
        next_topics = []
        for group in groups:
            required = {key for topic in group for key in topic['items']}
            next_topics.extend(await plan(group, required))
        topics = next_topics
        if len(groups) == 1:
            break
    else:
        raise InferenceError('Summary reduction exceeded bounded context budget')
    if not topics:
        return NO_CONTENT, trace
    semaphore = asyncio.Semaphore(2)

    async def check(topic):
        if len(topic['items']) == 1:
            return [topic]
        async with semaphore:
            return await plan([topic], set(topic['items']), single_topic=True)

    checked = await asyncio.gather(*(check(topic) for topic in topics))
    revised, seen = [], {}
    for parent, children in zip(topics, checked, strict=True):
        for child in children:
            key = child['title'].casefold()
            if key in seen:
                previous = revised[seen[key]]
                if set(previous['items']) & set(child['items']):
                    previous['items'] = list(dict.fromkeys(previous['items'] + child['items']))
                    previous['scope'] += ' ' + child['scope']
                    continue
                child['title'] = parent['title'] + ': ' + child['title']
                key = child['title'].casefold()
                if key in seen:
                    raise InferenceError('Invalid LLM response')
            seen[key] = len(revised)
            revised.append(child)
    topics = revised

    async def write(topic):
        selected = [lookup[key] for key in topic['items']]
        refs = sorted({key for item in selected for key in item['sources']})
        evidence = '\n\n'.join(item['context'] + '\n' + item['text'] for item in selected)
        # Notes organize topics but cannot establish action state. Write from the
        # original cited speech, including its qualifications and surrounding context.
        # Large topics retain the existing bounded, citation-checked reducer.
        if original_sources is not None:
            originals = {source['id']: source for source in original_sources}
            if any(key not in originals for key in refs):
                raise InferenceError('Model cited an unknown source')
            inputs = [source for source in original_sources if source['id'] in refs]
        else:
            inputs = [{'id': refs[0], 'text': evidence, 'started_at': '',
                       'citation_source_ids': refs}]
        task = (
            'a concise paragraph for ONLY the planned topic '
            + json.dumps(topic['title'], ensure_ascii=False) + '. Scope: '
            + topic['scope'] + '. The title and scope are organizational hints, not facts. '
            'Use only the supporting evidence. Write a few focused sentences preserving useful '
            'outcomes, follow-ups, dates, conditions and uncertainty. Omit other subjects and '
            'opaque fragments. Do not add a title, headings, bullets, introduction or conclusion. '
            'State each point once. Keep changed plans and conflicts explicit; a later mention '
            'alone does not establish agreement, cancellation or completion. '
            'Resolve proposed, planned, underway and completed states from the original speech, '
            'not the topic label or scope. Do not upgrade exploring an option to execution, '
            'readiness to deployment, or a completed component to a completed project. '
            'An agreed requirement or roadmap goal does not select a suggested tool or '
            'implementation. Keep open solutions open. Before returning, check each action '
            'against the original wording for its specific object, modality and temporal frame. '
            'Cite original sources for the facts you retain'
        )
        async with semaphore:
            answer, part_trace = await inference.summarize_traced(inputs, task)
        trace['model_calls'] += part_trace['model_calls']
        trace['reduction_used'] |= part_trace['reduction_used']
        if answer == NO_CONTENT or re.search(r'^\s*#{1,6}\s+', answer, re.M):
            raise InferenceError('Invalid LLM response')
        validate_citations(answer, [{'id': key} for key in refs])
        return f"## {topic['title']}\n{answer.strip()}"

    tasks = [asyncio.create_task(write(topic)) for topic in topics]
    try:
        sections = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    trace['final_prompt_source_ids'] = sorted({
        key for topic in topics for item in topic['items'] for key in lookup[item]['sources']
    })
    trace['topic_count'] = len(topics)
    return '\n\n'.join(sections), trace
