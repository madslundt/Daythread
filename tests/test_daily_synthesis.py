"""Daily grouping is a separate decision from writing each scoped summary."""

import json
import re
from uuid import uuid4

import httpx
import pytest

from omi_local.daily_synthesis import batches, note_items, parse_plan
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
        if payload['task'].startswith('Organize useful recorded information'):
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
    assert len(calls) == trace['model_calls'] == count + 2
    assert trace['topic_count'] == count


@pytest.mark.anyio
async def test_unknown_plan_items_get_one_repair_before_any_topic_is_written(tmp_path):
    key = str(uuid4())
    sources = [{'id': key, 'started_at': '2026-10-20T10:00:00Z',
                'text': f'## Launch\nReview on Friday. [{key}]'}]
    plans = []

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        if payload['task'].startswith('Organize useful recorded information'):
            plans.append(payload)
            return reply(json.dumps({'topics': [{'title': 'Launch', 'scope': 'Release review',
                                                 'items': ['n999' if len(plans) == 1 else 'n1']}]}))
        assert len(plans) == 3
        return reply(f'Review on Friday. [{key}]')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_daily_traced(sources, 'a daily recap')
    assert result.startswith('## Launch\n') and trace['model_calls'] == 4
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
    assert trace['model_calls'] == (6 if mixed else 4)


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
        if payload['task'].startswith('Organize useful recorded information'):
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
        if payload['task'].startswith('Organize useful recorded information'):
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
