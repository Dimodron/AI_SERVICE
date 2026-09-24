"""Integration tests. Run only with a disposable PostgreSQL and TEST_DATABASE=1.

Apply database/init.sql, set PGHOST/PGUSER/PGDATABASE and PYTHONPATH=app,
then run: python -m unittest discover -s tests -v
"""
import json
import os
import unittest
from uuid import uuid4

import httpx
import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import app
from services import QueenModels


@unittest.skipUnless(os.environ.get('TEST_DATABASE') == '1', 'Requires disposable test database')
class ChatScenariosTest(unittest.TestCase):
    def setUp(self):
        with psycopg.connect() as db:
            db.execute('TRUNCATE system_prompt, scenarios, conversations CASCADE')
            db.execute('DROP TABLE IF EXISTS scenario_test_debts')
            db.execute('CREATE TABLE scenario_test_debts (jurpers bigint, organization bigint, amount numeric, note text)')
            db.execute("INSERT INTO scenario_test_debts VALUES (7, 10, 100, 'A'), (7, 20, 200, 'B'), (8, 10, 900, 'C')")
            db.execute("INSERT INTO system_prompt (order_num, prompt) VALUES (2, 'SECOND'), (1, 'FIRST')")
            db.execute("INSERT INTO system_prompt (prompt, is_active) VALUES ('DISABLED', false)")
            self.scenario_id = str(db.execute(
                'INSERT INTO scenarios (title, table_name, columns_description, scenario) VALUES (%s, %s, %s, %s) RETURNING id',
                ('Debt', 'scenario_test_debts', Jsonb({'amount': 'Debt amount', 'note': 'Note', 'organization': 'Organization'}), 'Sum amount'),
            ).fetchone()[0])
        self.calls = []
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.real_ollama = QueenModels._client
        self.mock_ollama = httpx.AsyncClient(transport=httpx.MockTransport(self.respond), base_url='http://ollama')
        QueenModels._client = self.mock_ollama
        self.tool_arguments = {'scenario_id': self.scenario_id, 'aggregates': [{'function': 'sum', 'column': 'amount'}]}
        self.force_calls = False
        self.no_tools = False
        self.fail_chat = False

    def tearDown(self):
        self.client_context.portal.call(self.mock_ollama.aclose)
        QueenModels._client = self.real_ollama
        self.client_context.__exit__(None, None, None)

    def respond(self, request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'test'}]})
        self.assertEqual(request.url.path, '/api/chat')
        if self.fail_chat:
            return httpx.Response(500, json={'error': 'Test model failure'})
        body = json.loads(request.content)
        self.calls.append(body)
        messages = body['messages']
        self.assertEqual([m['content'] for m in messages[:2]], ['FIRST', 'SECOND'])
        self.assertNotIn('DISABLED', json.dumps(messages))
        if self.no_tools:
            return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': 'по этим данным информация отсутствует'}})
        if messages[-1]['role'] == 'tool' and not self.force_calls:
            content = messages[-1]['content']
            return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': content}})
        self.assertIn('tools', body)
        return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'query_scenario', 'arguments': self.tool_arguments}},
        ]}})

    def chat(self, **changes):
        if changes.get('save_history') and 'conversation_id' not in changes:
            created = self.client.post('/api/chat/create', json={})
            self.assertEqual(created.status_code, 201, created.text)
            changes['conversation_id'] = created.json()['conversation_id']
        return self.client.post('/api/chat', json={
            'message': 'Какая задолженность?', 'user_login': 'test', 'user_jurpers': 7, **changes,
        })

    def test_sum_scoped_by_jurpers_and_optional_organization(self):
        response = self.chat()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(response.json()['response'])['rows'], [{'sum_amount': '300'}])
        response = self.chat(user_organization=10)
        self.assertEqual(json.loads(response.json()['response'])['rows'], [{'sum_amount': '100'}])
        response = self.chat(user_organization=999)
        self.assertEqual(json.loads(response.json()['response'])['rows'], [{'sum_amount': None}])

    def test_saved_history_contains_only_user_and_final_answer(self):
        response = self.chat(save_history=True)
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()['conversation_id']
        response = self.chat(save_history=True, conversation_id=identifier, user_organization=10)
        self.assertEqual(response.status_code, 200, response.text)
        with psycopg.connect() as db:
            rows = db.execute('SELECT role FROM messages WHERE conversation_id=%s ORDER BY id', (identifier,)).fetchall()
        self.assertEqual(rows, [('user',), ('assistant',), ('user',), ('assistant',)])

    def test_no_scenarios_and_fresh_prompt_loading(self):
        with psycopg.connect() as db:
            db.execute('UPDATE scenarios SET is_active=false')
        self.no_tools = True
        response = self.chat()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('tools', self.calls[-1])
        with psycopg.connect() as db:
            db.execute("INSERT INTO system_prompt (order_num, prompt) VALUES (3, 'NEW')")
        self.chat()
        self.assertIn('NEW', [m['content'] for m in self.calls[-1]['messages']])

    def test_invalid_query_does_not_break_history_transaction(self):
        self.tool_arguments = {'scenario_id': self.scenario_id, 'aggregates': [{'function': 'sum', 'column': 'note'}]}
        response = self.chat(save_history=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('error', json.loads(response.json()['response']))
        with psycopg.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 2)

    def test_unknown_scenario_and_column_are_rejected(self):
        for arguments in (
            {'scenario_id': str(uuid4())},
            {'scenario_id': self.scenario_id, 'columns': ['secret']},
            {'scenario_id': self.scenario_id, 'sql': 'DELETE FROM users'},
        ):
            self.tool_arguments = arguments
            response = self.chat()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('error', json.loads(response.json()['response']))

    def test_filter_values_are_parameters_and_results_signal_truncation(self):
        self.tool_arguments = {'scenario_id': self.scenario_id, 'columns': ['amount'], 'filters': [{'column': 'note', 'value': "' OR TRUE --"}]}
        response = self.chat()
        self.assertEqual(json.loads(response.json()['response'])['rows'], [])
        self.tool_arguments = {'scenario_id': self.scenario_id, 'columns': ['amount'], 'limit': 1}
        response = self.chat()
        result = json.loads(response.json()['response'])
        self.assertEqual(len(result['rows']), 1)
        self.assertTrue(result['truncated'])

    def test_missing_scope_column_fails_closed(self):
        with psycopg.connect() as db:
            db.execute('ALTER TABLE scenario_test_debts DROP COLUMN jurpers')
        response = self.chat()
        self.assertIn('error', json.loads(response.json()['response']))

    def test_organization_cannot_be_overridden_by_model(self):
        self.tool_arguments = {'scenario_id': self.scenario_id, 'filters': [{'column': 'organization', 'value': 20}]}
        response = self.chat(user_organization=10)
        self.assertEqual(json.loads(response.json()['response'])['rows'], [])

    def test_grouped_count(self):
        self.tool_arguments = {'scenario_id': self.scenario_id, 'group_by': ['organization'],
                               'aggregates': [{'function': 'count', 'column': '*'}]}
        response = self.chat()
        rows = json.loads(response.json()['response'])['rows']
        self.assertEqual(sorted(rows, key=lambda row: row['organization']), [
            {'organization': 10, 'count_all': 1}, {'organization': 20, 'count_all': 1},
        ])

    def test_missing_organization_column_fails_closed(self):
        with psycopg.connect() as db:
            db.execute('ALTER TABLE scenario_test_debts DROP COLUMN organization')
            db.execute("UPDATE scenarios SET columns_description = columns_description - 'organization'")
        response = self.chat(user_organization=10)
        self.assertIn('error', json.loads(response.json()['response']))

    def test_create_chat_does_not_generate_answer(self):
        response = self.client.post('/api/chat/create', json={'title': 'Файлы'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['title'], 'Файлы')
        self.assertEqual(response.json()['model'], 'test')
        self.assertEqual(self.calls, [])
        history = self.client.post('/api/chat/history', json={'conversation_id': response.json()['conversation_id']})
        self.assertEqual(history.json(), {'history': []})

    def test_message_requires_existing_chat_when_saving(self):
        response = self.client.post('/api/chat', json={
            'message': 'Hello', 'save_history': True, 'user_login': 'test', 'user_jurpers': 7,
        })
        self.assertEqual(response.status_code, 422, response.text)
        response = self.chat(save_history=True, conversation_id=str(uuid4()))
        self.assertEqual(response.status_code, 404, response.text)
        with psycopg.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM conversations').fetchone()[0], 0)

    def test_create_upload_ask_and_reuse_attachment(self):
        self.no_tools = True
        created = self.client.post('/api/chat/create', json={}).json()
        identifier = created['conversation_id']
        upload = self.client.post('/api/files', files={'file': ('report.txt', b'Debt is 100 rubles', 'text/plain')})
        self.assertEqual(upload.status_code, 201, upload.text)
        file_id = upload.json()['file_id']
        response = self.chat(save_history=True, conversation_id=identifier, file_ids=[file_id])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('Debt is 100 rubles', json.dumps(self.calls[-1]['messages']))
        response = self.chat(save_history=True, conversation_id=identifier)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('Debt is 100 rubles', json.dumps(self.calls[-1]['messages']))
        with psycopg.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM conversation_files WHERE conversation_id=%s', (identifier,)).fetchone()[0], 1)
            self.assertIsNotNone(db.execute('SELECT last_message_at FROM conversations WHERE id=%s', (identifier,)).fetchone()[0])

    def test_failure_preserves_created_chat_and_uploaded_file(self):
        identifier = self.client.post('/api/chat/create', json={}).json()['conversation_id']
        file_id = self.client.post('/api/files', files={'file': ('report.txt', b'Test report', 'text/plain')}).json()['file_id']
        self.fail_chat = True
        response = self.chat(save_history=True, conversation_id=identifier, file_ids=[file_id])
        self.assertEqual(response.status_code, 502, response.text)
        with psycopg.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM conversations WHERE id=%s', (identifier,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE conversation_id=%s', (identifier,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM conversation_files WHERE conversation_id=%s', (identifier,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM files WHERE id=%s', (file_id,)).fetchone()[0], 1)
        self.fail_chat = False
        self.no_tools = True
        response = self.chat(save_history=True, conversation_id=identifier, file_ids=[file_id])
        self.assertEqual(response.status_code, 200, response.text)

    def test_missing_attachment_returns_404(self):
        response = self.chat(save_history=True, file_ids=[str(uuid4())])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.calls, [])

    def test_loop_is_bounded(self):
        self.force_calls = True
        response = self.chat(save_history=True)
        self.assertEqual(response.status_code, 422, response.text)
        with psycopg.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
