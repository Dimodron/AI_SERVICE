import os
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from fastapi import HTTPException
from database.history import connect, database_lifespan
from schemas.prompts import PromptCreate, PromptUpdate, PromptResponse
from schemas.scenarios import ScenarioCreate, ScenarioUpdate, ScenarioResponse
from services import prompts, scenarios


@skipUnless(os.getenv('TEST_DATABASE') == '1', 'requires disposable PostgreSQL')
class AuthorTests(IsolatedAsyncioTestCase):
    async def test_author_login_roundtrip_and_legacy_records(self):
        async with database_lifespan():
            async with await connect() as conn:
                await conn.execute(Path('/database/init.sql').read_text())
                await conn.execute("INSERT INTO users(login) VALUES ('author-test'), ('editor-test')")
            for service,create,update,response,body in (
                (prompts,PromptCreate,PromptUpdate,PromptResponse,{'prompt':'text'}),
                (scenarios,ScenarioCreate,ScenarioUpdate,ScenarioResponse,{'title':'test','scenario':'text'}),
            ):
                name = 'prompt' if service is prompts else 'scenario'
                make = getattr(service, 'create_'+name)
                edit = getattr(service, 'update_'+name)
                get = getattr(service, 'get_'+name)
                listing = getattr(service, 'list_'+name+'s')
                row = await make(create(**body, create_user='author-test'))
                response.model_validate(row)
                self.assertEqual(row['create_user'], 'author-test')
                row = await edit(row['id'], update(edit_user='editor-test'))
                self.assertEqual(row['edit_user'], 'editor-test')
                self.assertEqual((await get(row['id']))['create_user'], 'author-test')
                self.assertTrue(any(r['id']==row['id'] and r['edit_user']=='editor-test' for r in await listing(None,100,0)))
                async with await connect() as conn:
                    table = 'system_prompt' if service is prompts else 'scenarios'
                    stored = await (await conn.execute(f'SELECT create_user FROM {table} WHERE id=%s', (row['id'],))).fetchone()
                    self.assertNotEqual(str(stored['create_user']), 'author-test')
                row = await edit(row['id'], update(edit_user=None))
                self.assertIsNone(row['edit_user'])
                with self.assertRaises(HTTPException) as error:
                    await make(create(**body, create_user='missing-author'))
                self.assertEqual(error.exception.status_code,422)
