"""Run with TEST_DATABASE=1 against a disposable PostgreSQL database."""
import os
import unittest
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import oracledb
from fastapi import FastAPI

from database.history import database_lifespan, connect
from routers.data_import_router import router
from schemas.data_import import ImportRequest
from services.data_import import duplicate_tables


class OracleCursor:
    def __init__(self, datasets):
        self.datasets = datasets
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    async def execute(self, query, params=None):
        self.queries.append((query, params))
        if not query.startswith('SELECT'):
            return
        name = query.split('"')[3]
        columns, rows = self.datasets[name]
        self.description = [SimpleNamespace(name=name, type_code=kind) for name, kind in columns]
        self.rows = [] if query.endswith(" WHERE 1 = 0") else list(rows)
        if params:
            self.rows = [row for row in rows if all(str(row[[c[0] for c in columns].index(part.split('"')[1])]) == params[f'v{i}'] for i, part in enumerate(query.split(' WHERE ')[1].split(' AND ')))]

    async def fetchmany(self, size):
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows


class OracleConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def cursor(self):
        return self._cursor


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_validation(self):
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.dict(os.environ, {'DATA_IMPORT_TOKEN': ''}):
                self.assertEqual((await client.post('/api/dublicate?table=DEBTS')).status_code, 503)
            with patch.dict(os.environ, {'DATA_IMPORT_TOKEN': 'test'}):
                self.assertEqual((await client.post('/api/dublicate?table=DEBTS')).status_code, 403)
                for query in ('table=x;drop', 'table=A,a', 'table=A&filter=VERSION', 'table=A&version=1&filter=X&values=2', 'table=A&filter=X,x&values=1,2'):
                    self.assertEqual((await client.post('/api/dublicate?' + query, headers={'X-Import-Token': 'test'})).status_code, 422)


    async def test_apex_combines_version_and_custom_filter(self):
        from unittest.mock import AsyncMock
        app = FastAPI()
        app.include_router(router)
        result = {'mode': 'append', 'tables': [{'table': 'oracle_data.debts', 'rows': 2}]}
        mocked = AsyncMock(return_value=result)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.dict(os.environ, {'DATA_IMPORT_TOKEN': 'test'}), patch('routers.data_import_router.duplicate_tables', mocked):
                response = await client.post('/api/dublicate', params={
                    'table': 'DEBTS', 'filter': 'version,jur_pers',
                    'values': '570534214,1351099', 'mode': 'append'
                }, headers={'X-Import-Token': 'test'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), result)
        request = mocked.call_args.args[0]
        self.assertEqual(request.filters, {'version': '570534214', 'jur_pers': '1351099'})
        self.assertEqual(request.mode, 'append')


@unittest.skipUnless(os.getenv('TEST_DATABASE') == '1', 'requires disposable PostgreSQL')
class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = database_lifespan()
        await self.lifespan.__aenter__()
        async with await connect() as connection:
            await connection.execute('DROP SCHEMA IF EXISTS oracle_data CASCADE')
            await connection.execute((Path(__file__).resolve().parents[1] / 'database/import_history.sql').read_text())
            await connection.execute('TRUNCATE public.data_import_history')
        self.environment = patch.dict(os.environ, {'ORACLE_HOST': 'test', 'ORACLE_USER': 'reader', 'ORACLE_PASS': 'test', 'ORACLE_NAME': 'test'})
        self.environment.start()

    async def asyncTearDown(self):
        self.environment.stop()
        await self.lifespan.__aexit__(None, None, None)

    async def run_import(self, datasets, **options):
        cursor = OracleCursor(datasets)
        with patch('services.data_import.oracledb.connect_async', return_value=OracleConnection(cursor)):
            return await duplicate_tables(ImportRequest(tables=list(datasets), **options))

    async def test_history_tracks_commits_failures_and_filtered_refresh(self):
        from services.import_history import history
        async with await connect() as conn:
            await conn.execute('TRUNCATE public.data_import_history')
        columns = [('VERSION', oracledb.DB_TYPE_NUMBER)]
        await self.run_import({'DEBTS': (columns, [(1,)])})
        initial = await history('DEBTS')
        self.assertEqual(initial['items'][0]['row_count'], 1)
        self.assertEqual(initial['items'][0]['status'], 'success')
        last_success = initial['last_success_at']
        with self.assertRaises(Exception):
            await self.run_import({'DEBTS': (columns, [(2,)]), 'BAD': ([('BROKEN', object())], [])})
        failed = await history('DEBTS')
        self.assertEqual(failed['last_success_at'], last_success)
        self.assertEqual(failed['items'][0]['status'], 'error')
        self.assertIsNone(failed['items'][0]['row_count'])
        self.assertEqual(await self.rows(), [{'version': Decimal(1)}])
        await self.run_import({'DEBTS': (columns, [])}, filters={'VERSION': '2'}, mode='append')
        refreshed = await history('debts', limit=1)
        self.assertTrue(refreshed['has_more'])
        self.assertEqual(refreshed['items'][0]['row_count'], 0)
        self.assertEqual(refreshed['items'][0]['filters'], {'VERSION': '2'})
        self.assertEqual(refreshed['items'][0]['mode'], 'append')
        self.assertGreater(refreshed['last_success_at'], last_success)
        second = await history('DEBTS', limit=1, offset=1)
        self.assertEqual(second['items'][0]['status'], 'error')
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.dict(os.environ, {'DATA_IMPORT_TOKEN': 'test'}):
                self.assertEqual((await client.get('/api/dublicate/history')).status_code, 403)
                response = await client.get('/api/dublicate/history?table=DEBTS', headers={'X-Import-Token':'test'})
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.json()['last_success_at'], str)
                self.assertEqual(len(response.json()['items']), 3)

    async def rows(self):
        async with await connect() as connection:
            return await (await connection.execute('SELECT * FROM oracle_data.debts ORDER BY version')).fetchall()

    async def test_replace_append_and_decimal(self):
        columns = [('VERSION', oracledb.DB_TYPE_NUMBER), ('JURPERS', oracledb.DB_TYPE_NUMBER), ('AMOUNT', oracledb.DB_TYPE_NUMBER)]
        amount = Decimal('12345678901234567890.123456789')
        result = await self.run_import({'DEBTS': (columns, [(1, 10, amount), (2, 20, Decimal('2'))])})
        self.assertEqual(result['tables'][0]['rows'], 2)
        self.assertEqual((await self.rows())[0]['amount'], amount)
        for _ in range(2):
            await self.run_import({'DEBTS': (columns, [(1, 10, Decimal('9')), (2, 20, Decimal('99'))])}, filters={'version': '1'}, mode='append')
        rows = await self.rows()
        self.assertEqual([r['amount'] for r in rows], [Decimal('9'), Decimal('2')])
        await self.run_import({'DEBTS': (columns, [])}, mode='append')
        self.assertEqual(await self.rows(), [])

    async def test_atomic_rollback_across_tables(self):
        columns = [('VERSION', oracledb.DB_TYPE_NUMBER)]
        await self.run_import({'DEBTS': (columns, [(1,)])})
        with self.assertRaises(Exception):
            await self.run_import({'DEBTS': (columns, [(2,)]), 'BROKEN': (columns, [('not a number',)])})
        self.assertEqual(await self.rows(), [{'version': Decimal(1)}])

    async def test_structure_conflict_preserves_data(self):
        await self.run_import({'DEBTS': ([('VERSION', oracledb.DB_TYPE_NUMBER)], [(1,)])})
        with self.assertRaises(Exception) as failure:
            await self.run_import({'DEBTS': ([('OTHER', oracledb.DB_TYPE_VARCHAR)], [('text',)])}, mode='append')
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(await self.rows(), [{'version': Decimal(1)}])

    async def test_scenario_reads_imported_data_with_user_scope(self):
        from uuid import uuid4
        from schemas.qwen import ChatRequest
        from schemas.scenario_query import ScenarioQuery
        from services.scenario_runner import query_scenario
        columns = [(name, oracledb.DB_TYPE_NUMBER) for name in ('JURPERS', 'ORGANIZATION', 'AMOUNT')]
        await self.run_import({'DEBTS': (columns, [(10, 1, 100), (10, 2, 200), (20, 1, 999)])})
        scenario = {'visible_jurpers': [], 'table_name': 'oracle_data.debts', 'columns_description': {'amount': 'Amount'}}
        async with await connect() as connection:
            result = await query_scenario(connection, scenario,
                ScenarioQuery(scenario_id=uuid4(), columns=['amount']),
                ChatRequest(message='debt', user_login='test', user_jurpers=10, user_organization=1))
        self.assertEqual(result['rows'], [{'amount': Decimal(100)}])

    async def test_timeout_rolls_back(self):
        columns = [('VERSION', oracledb.DB_TYPE_NUMBER)]
        await self.run_import({'DEBTS': (columns, [(1,)])})
        cursor = OracleCursor({'DEBTS': (columns, [(2,)])})
        original_fetch = cursor.fetchmany
        async def timed_fetch(size):
            if not cursor.rows:
                raise TimeoutError()
            return await original_fetch(size)
        cursor.fetchmany = timed_fetch
        with patch('services.data_import.oracledb.connect_async', return_value=OracleConnection(cursor)):
            with self.assertRaises(Exception) as failure:
                await duplicate_tables(ImportRequest(tables=['DEBTS']))
        self.assertEqual(failure.exception.status_code, 504)
        self.assertEqual(await self.rows(), [{'version': Decimal(1)}])

    async def test_binary_json_and_timestamp(self):
        from datetime import datetime, timezone
        columns = [('VERSION', oracledb.DB_TYPE_NUMBER), ('RAW', oracledb.DB_TYPE_RAW),
                   ('DOC', oracledb.DB_TYPE_JSON), ('TIME', oracledb.DB_TYPE_TIMESTAMP_LTZ)]
        await self.run_import({'DEBTS': (columns, [(1, b'\x00\xff', {'name': 'тест'}, datetime(2026, 1, 1))])})
        row = (await self.rows())[0]
        self.assertEqual(row['raw'], b'\x00\xff')
        self.assertEqual(row['doc'], {'name': 'тест'})
        self.assertEqual(row['time'], datetime(2026, 1, 1, tzinfo=timezone.utc))

    async def test_quoted_lowercase_filters_and_append(self):
        columns = [('login', oracledb.DB_TYPE_VARCHAR), ('version', oracledb.DB_TYPE_NUMBER)]
        await self.run_import({'DEBTS': (columns, [('ZVEREV', 1), ('OTHER', 2)])})
        for key in ('login', 'LOGIN', 'Login'):
            await self.run_import({'DEBTS': (columns, [('ZVEREV', 3), ('OTHER', 99)])},
                                  filters={key: 'ZVEREV'}, mode='append')
        self.assertEqual(await self.rows(), [{'login': 'OTHER', 'version': Decimal(2)},
                                             {'login': 'ZVEREV', 'version': Decimal(3)}])

    async def test_unknown_filter_preserves_previous_copy(self):
        columns = [('VERSION', oracledb.DB_TYPE_NUMBER)]
        await self.run_import({'DEBTS': (columns, [(1,)])})
        with self.assertRaises(Exception) as failure:
            await self.run_import({'DEBTS': (columns, [(2,)])}, filters={'login': 'ZVEREV'})
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(await self.rows(), [{'version': Decimal(1)}])

    async def test_batches_and_nulls(self):
        data = [(i, None if i % 2 else 'данные') for i in range(2101)]
        result = await self.run_import({'DEBTS': ([('VERSION', oracledb.DB_TYPE_NUMBER), ('TEXT', oracledb.DB_TYPE_VARCHAR)], data)})
        self.assertEqual(result['tables'][0]['rows'], 2101)
        self.assertEqual(len(await self.rows()), 2101)


if __name__ == '__main__':
    unittest.main()
