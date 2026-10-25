"""Daily grouping is a separate decision from writing each scoped summary."""

import json
import re
from uuid import uuid4

import httpx
import pytest

from omi_local.daily_synthesis import batches, note_items, parse_plan, parse_review
from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def reply(text):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                                                'message': {'content': text}}]})


@pytest.mark.anyio
@pytest.mark.parametrize('count', [1, 12])
async def test_plan_controls_flexible_count_and_writers_receive_only_supporting_items(
    tmp_path, count,
):
    ids = [str(uuid4()) for _ in range(count)]
    notes = '\n'.join(f'* **Subject {n}**: Review deadline {n} is Friday. [{key}]'
                      for n, key in enumerate(ids))
    sources = [{'id': ids[0], 'started_at': '2026-10-20T10:00:00Z',
                'text': '## Broad project updates\n' + notes, 'citation_source_ids': ids}]
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(payload)
        if payload['task'].startswith(('Organize useful recorded information',
                                       'Review the complete daily topic plan')):
            rows = json.loads(payload['evidence'])
            if isinstance(rows, dict):
                return reply(json.dumps({'topics': rows['proposed_topics']}))
            assert len(rows) == count and all(key not in payload['evidence'] for key in ids)
            assert all(row['context'].startswith('Subject ') for row in rows)
            return reply(json.dumps({'topics': [
                {'title': f'Subject {n}', 'scope': f'Only deadline {n}', 'items': [row['id']]}
                for n, row in enumerate(rows)]}))
        found = re.findall(r'\[([0-9a-f-]{36})\]', payload['evidence'])
        assert len(set(found)) == 1  # The broad heading cannot mix writers' subjects.
        return reply(f'Review is due Friday. [{found[0]}]')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(sources, 'a daily recap')
    assert re.findall(r'^## (.+)$', result, re.M) == [f'Subject {n}' for n in range(count)]
    assert all(key in result for key in ids)
    assert len(calls) == trace['model_calls'] == count + 3
    assert trace['topic_count'] == count


@pytest.mark.anyio
async def test_unknown_plan_items_get_one_repair_before_any_topic_is_written(tmp_path):
    key = str(uuid4())
    sources = [{'id': key, 'started_at': '2026-10-20T10:00:00Z',
                'text': f'## Launch\nReview on Friday. [{key}]'}]
    plans = []

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        if payload['task'].startswith('Review the complete daily topic plan'):
            return reply(json.dumps({'topics': json.loads(payload['evidence'])['proposed_topics']}))
        if payload['task'].startswith('Organize useful recorded information'):
            plans.append(payload)
            return reply(json.dumps({'topics': [{'title': 'Launch', 'scope': 'Release review',
                                                 'items': ['n999' if len(plans) == 1 else 'n1']}]}))
        assert len(plans) == 3
        return reply(f'Review on Friday. [{key}]')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(sources, 'a daily recap')
    assert result.startswith('## Launch\n') and trace['model_calls'] == 5
    assert 'previous plan was invalid' in plans[1]['task']


def test_plan_reconciliation_preserves_every_selected_item_and_rejects_unknown_ids():
    valid = json.dumps({'topics': [{'title': 'Workflow', 'scope': 'Connected issues',
                                   'items': ['n1', 'n2']}]})
    assert parse_plan(valid, {'n1', 'n2'}, {'n1', 'n2'})
    with pytest.raises(InferenceError):
        parse_plan(valid, {'n1'})
    with pytest.raises(InferenceError):
        parse_plan(valid.replace(', "n2"', ''), {'n1', 'n2'}, {'n1', 'n2'})


def test_long_note_paragraphs_and_planning_batches_keep_the_tail_and_source_provenance():
    key = str(uuid4())
    text = 'Initial decision. ' + 'Supporting context. ' * 1500 + f'Final follow-up. [{key}]'
    items = note_items([{'id': key, 'text': '## A useful subject\n' + text}])
    assert len(items) > 1 and 'Final follow-up.' in items[-1]['text']
    assert all(item['sources'] == [key] for item in items)
    groups = batches(items)
    assert len(groups) > 1 and [row['id'] for group in groups for row in group] == [
        item['id'] for item in items
    ]


@pytest.mark.anyio
@pytest.mark.parametrize('mixed', [False, True])
async def test_local_coherence_splits_unrelated_projects_and_keeps_one_workflow(tmp_path, mixed):
    ids = [str(uuid4()) for _ in range(3)]
    labels = ['Hiring', 'Catalog launch', 'Beta feedback'] if mixed else [
        'File latency', 'Locked files', 'Synchronization',
    ]
    notes = '\n'.join(f'* **{label}**: Review the issue on Friday. [{key}]'
                      for label, key in zip(labels, ids, strict=True))
    sources = [{'id': ids[0], 'started_at': '2026-10-20T10:00:00Z',
                'text': '## Provisional updates\n' + notes, 'citation_source_ids': ids}]
    checks = []

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        if payload['task'].startswith('Review the complete daily topic plan'):
            return reply(json.dumps({'topics': json.loads(payload['evidence'])['proposed_topics']}))
        if payload['task'].startswith('Decide whether ONE proposed'):
            checks.append(payload)
            supplied = json.loads(payload['evidence'])
            assert 'proposed_topics' not in supplied
            assert 'suggested_scope' in supplied
            assert all('parent_context' not in item for item in supplied['supporting_items'])
            topics = ([{'title': label, 'scope': label, 'items': [f'n{n+1}']}
                       for n, label in enumerate(labels)] if mixed else [{
                           'title': 'Ingestion workflow', 'scope': 'Same connected workflow',
                           'items': ['n1', 'n2', 'n3'],
                       }])
            return reply(json.dumps({'split_required': mixed, 'topics': topics}))
        if payload['task'].startswith('Organize useful recorded'):
            return reply(json.dumps({'topics': [{
                'title': 'Draft updates', 'scope': 'Provisional organization',
                'items': ['n1', 'n2', 'n3'],
            }]}))
        refs = list(dict.fromkeys(re.findall(r'\[([0-9a-f-]{36})\]', payload['evidence'])))
        assert len(refs) == (1 if mixed else 3)
        return reply('Friday review. ' + ' '.join(f'[{key}]' for key in refs))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(sources, 'a daily recap')
    assert len(checks) == 1
    if not mixed:
        assert result.startswith('## Draft updates\n')
    assert len(re.findall(r'^## ', result, re.M)) == (3 if mixed else 1)
    assert trace['model_calls'] == (7 if mixed else 5)


@pytest.mark.anyio
async def test_topic_writer_grounds_action_state_in_scoped_originals_not_drifting_notes(tmp_path):
    key, other_key = str(uuid4()), str(uuid4())
    notes = [{'id': key, 'started_at': '2026-10-20T10:00:00Z',
              'text': f'## Catalog\nThe team is deploying directly to production. '
                      f'Atlas integration was selected for Q1. [{key}]'}]
    original = ('The team is looking into how to deploy directly in production. '
                'The risk assessment was completed. We agreed to add user metrics in Q1. '
                'Atlas could be an option; the solution remains open.')
    originals = [{'id': key, 'started_at': '2026-10-20T10:00:00Z', 'text': original},
                 {'id': other_key, 'started_at': '2026-10-20T11:00:00Z',
                  'text': 'The unrelated beta application was released last Thursday. '
                          'That other project selected Atlas.'}]

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        if payload['task'].startswith(('Organize useful recorded information',
                                       'Review the complete daily topic plan')):
            return reply(json.dumps({'topics': [
                {'title': 'Catalog', 'scope': 'Catalog rollout', 'items': ['n1']}]}))
        assert original in payload['evidence']
        assert 'The team is deploying directly' not in payload['evidence']
        assert 'Atlas integration was selected' not in payload['evidence']
        assert 'unrelated beta application' not in payload['evidence']
        return reply(f'The team is considering direct deployment; the risk assessment '
                     f'was reported completed. User metrics are planned for Q1, with '
                     f'Atlas one option and the solution still open. [{key}]')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(
                                            notes, 'a daily recap', original_sources=originals)
    assert 'considering direct deployment' in result
    assert 'risk assessment was reported completed' in result
    assert 'Atlas one option and the solution still open' in result
    assert trace['writing_evidence'] == 'original_transcripts'
    assert trace['final_prompt_source_ids'] == [key]


@pytest.mark.anyio
async def test_topic_writer_keeps_action_updates_in_recording_order(tmp_path):
    first = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
    last = '00000000-0000-4000-8000-000000000001'
    notes = [{'id': first, 'started_at': '2026-10-20T10:00:00Z',
              'text': f'## Launch\nDeployment status changed. [{first}] [{last}]',
              'citation_source_ids': [first, last]}]
    originals = [{'id': first, 'started_at': '2026-10-20T10:00:00Z',
                  'text': 'We are considering deployment.'},
                 {'id': last, 'started_at': '2026-10-20T11:00:00Z',
                  'text': 'The deployment has now completed.'}]

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        if payload['task'].startswith(('Organize useful recorded information',
                                       'Review the complete daily topic plan')):
            return reply(json.dumps({'topics': [
                {'title': 'Launch', 'scope': 'Deployment status', 'items': ['n1']}]}))
        evidence = payload['evidence']
        assert evidence.index('considering deployment') < evidence.index('has now completed')
        return reply(f'Deployment was proposed and later reported completed. [{first}] [{last}]')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, _ = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                    ).summarize_daily_traced(
                                        notes, 'a daily recap', original_sources=originals)
    assert 'later reported completed' in result


@pytest.mark.anyio
@pytest.mark.parametrize('split_before_review', [False, True])
async def test_whole_day_review_merges_recurring_themes_and_accounts_for_incidental_items(
    tmp_path, split_before_review,
):
    ids = [str(uuid4()) for _ in range(4)]
    facts = [
        'AI governance: agree human approval before autonomous agents deploy.',
        'Agent autonomy: monitor agents and require human approval for deployment.',
        'The speaker thanked everyone and invited LinkedIn connections.',
        'Remember to collect the repaired bicycle on Monday.',
    ]
    notes = [{'id': key, 'started_at': '2026-10-23T10:00:00Z',
              'text': f'## Recording {index}\n{fact} [{key}]'}
             for index, (key, fact) in enumerate(zip(ids, facts, strict=True))]
    reviews, writers = [], []
    purpose = 'Keep ordinary errands; omit routine closing remarks and speaker introductions.'

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        task = payload['task']
        if task.startswith('Organize useful recorded information'):
            supplied = json.loads(payload['evidence'])
            topics = supplied['proposed_topics'] if isinstance(supplied, dict) else [
                {'title': f'Draft {n}', 'scope': fact, 'items': [f'n{n+1}']}
                for n, fact in enumerate(facts)]
            if split_before_review:
                topics = [{'title': 'Session', 'scope': 'All session notes',
                           'items': ['n1', 'n2', 'n3', 'n4']}]
            return reply(json.dumps({'topics': topics}))
        if task.startswith('Decide whether ONE proposed'):
            assert split_before_review
            return reply(json.dumps({'split_required': True, 'topics': [
                {'title': f'Draft {n}', 'scope': fact, 'items': [f'n{n+1}']}
                for n, fact in enumerate(facts)]}))
        if task.startswith('Review the complete daily topic plan'):
            reviews.append(payload)
            assert purpose in task
            supplied = json.loads(payload['evidence'])
            assert {key for topic in supplied['proposed_topics'] for key in topic['items']} == {
                'n1', 'n2', 'n3', 'n4',
            }
            return reply(json.dumps({'topics': [
                {'title': 'AI governance and autonomy', 'scope': 'Human approval and monitoring',
                 'items': ['n1', 'n2']},
                {'title': 'Bicycle collection', 'scope': 'Collect on Monday', 'items': ['n4']},
            ], 'omitted_items': [{'id': 'n3', 'reason': 'Routine closing remarks.'}]}))
        writers.append(payload)
        refs = list(dict.fromkeys(re.findall(r'\[([0-9a-f-]{36})\]', payload['evidence'])))
        return reply('Useful recorded information. ' + ' '.join(f'[{key}]' for key in refs))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(notes, purpose)
    assert re.findall(r'^## (.+)$', result, re.M) == [
        'AI governance and autonomy', 'Bicycle collection',
    ]
    assert len(reviews) == 1 and len(writers) == 2
    assert all(key in result for key in (ids[0], ids[1], ids[3]))
    assert ids[2] not in result
    assert trace['topic_count'] == 2
    assert trace['omitted_item_count'] == 1


@pytest.mark.parametrize('omitted,selected', [
    ([], ['n1']),
    ([{'id': 'n2', 'reason': ''}], ['n1']),
    ([{'id': 'n999', 'reason': 'Chatter'}], ['n1', 'n2']),
    ([{'id': 'n2', 'reason': 'Chatter'}], ['n1', 'n2']),
    ([{'id': 'n2', 'reason': 'Chatter'}] * 2, ['n1']),
])
def test_final_review_rejects_unaccounted_or_invalid_omissions(omitted, selected):
    answer = json.dumps({'topics': [{'title': 'Plan', 'scope': 'Next steps', 'items': selected}],
                         'omitted_items': omitted})
    with pytest.raises(InferenceError):
        parse_review(answer, {'n1', 'n2'})


@pytest.mark.parametrize('answer', ['[]', 'null', '"not a plan"'])
def test_malformed_review_roots_raise_repairable_inference_errors(answer):
    with pytest.raises(InferenceError):
        parse_review(answer, {'n1'})


def test_final_review_can_explicitly_reject_all_incidental_material():
    answer = json.dumps({'topics': [], 'omitted_items': [
        {'id': 'n1', 'reason': 'Routine thanks with no later use.'},
    ]})
    assert parse_review(answer, {'n1'}) == ([], 1)
    with pytest.raises(InferenceError):
        parse_review(answer, {'n1'}, allow_omissions=False)


@pytest.mark.anyio
async def test_all_incidental_topics_across_review_batches_produce_no_content(
    tmp_path, monkeypatch,
):
    from omi_local import daily_synthesis
    from omi_local.summary_filter import NO_CONTENT

    monkeypatch.setattr(daily_synthesis, 'BUDGET', 600)
    key = str(uuid4())
    notes = [{'id': key, 'text': '\n'.join(
        f'* Routine closing remark {n}. [{key}]' for n in range(3)
    )}]
    reviews = []

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        task = payload['task']
        if task.startswith('Organize useful recorded'):
            return reply(json.dumps({'topics': [{
                'title': 'Session closing', 'scope': 'Closing remarks',
                'items': ['n1', 'n2', 'n3'],
            }]}))
        if task.startswith('Decide whether ONE proposed'):
            return reply(json.dumps({'split_required': True, 'topics': [
                {'title': f'Closing {n}', 'scope': 'Routine thanks. ' * 25, 'items': [f'n{n+1}']}
                for n in range(3)
            ]}))
        assert task.startswith('Review the complete daily topic plan')
        supplied = json.loads(payload['evidence'])
        reviews.append(supplied)
        return reply(json.dumps({'topics': [], 'omitted_items': [
            {'id': item['id'], 'reason': 'Routine closing remark without later use.'}
            for item in supplied['supporting_items']
        ]}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(notes, 'a selective daily recap')
    assert result == NO_CONTENT
    assert len(reviews) == 3
    assert trace['omitted_item_count'] == 3 and trace['topic_count'] == 0


@pytest.mark.anyio
async def test_review_without_supporting_facts_cannot_omit_a_hidden_useful_item(tmp_path):
    key = str(uuid4())
    notes = [{'id': key, 'text': ('Background. ' * 2600)
              + f'Remember to collect the bicycle on Monday. [{key}]'}]
    item_ids = [item['id'] for item in note_items(notes)]
    topic = {'title': 'Personal updates', 'scope': 'Background and later arrangements',
             'items': item_ids}
    reviews = []

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        task = payload['task']
        if task.startswith('Organize useful recorded'):
            supplied = json.loads(payload['evidence'])
            rows = supplied['proposed_topics'] if isinstance(supplied, dict) else supplied
            selected = [key for row in rows for key in
                        ([row['id']] if 'id' in row else row['items'])]
            return reply(json.dumps({'topics': [{**topic, 'items': selected}]}))
        if task.startswith('Decide whether ONE proposed'):
            return reply(json.dumps({'split_required': False, 'topics': [topic]}))
        if task.startswith('Review the complete daily topic plan'):
            reviews.append(payload)
            assert 'supporting_items' not in json.loads(payload['evidence'])
            assert 'preserve EVERY supplied item ID and return no omitted_items' in task
            if len(reviews) == 1:
                return reply(json.dumps({'topics': [], 'omitted_items': [
                    {'id': item, 'reason': 'Only background.'} for item in item_ids
                ]}))
            return reply(json.dumps({'topics': [topic]}))
        return reply(f'Collect the bicycle on Monday. [{key}]')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(notes, 'a daily recap')
    assert 'Collect the bicycle on Monday' in result
    assert len(reviews) == 2 and 'previous plan was invalid' in reviews[1]['task']
    assert trace['omitted_item_count'] == 0
