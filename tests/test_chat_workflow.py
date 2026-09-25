import json
import os
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from database.history import connect, database_lifespan
from schemas.qwen import AttachFilesRequest, ChatRequest, HistoryRequest
from schemas.scenario_query import ScenarioQuery
from services.chat import attach_files, chat, history, delete_chat
from services.chat_context import load_chat_context
from services.scenario_runner import query_scenario
from services.user_context import user_context, trusted_chat_source


@skipUnless(os.getenv('TEST_DATABASE') == '1', 'requires disposable PostgreSQL')
class ChatWorkflowTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from config import settings
        profile_settings = patch('services.user_context.settings', settings.model_copy(update={
            'USER_CONTEXT_TABLE': 'oracle_data.gpt_user_context'}))
        profile_settings.start()
        self.addCleanup(profile_settings.stop)
        self.life = database_lifespan()
        await self.life.__aenter__()
        async with await connect() as conn:
            await conn.execute(Path('/database/init.sql').read_text())
            await conn.execute('TRUNCATE users, conversations, messages, files, conversation_files, message_files, scenarios, system_prompt CASCADE')
            await conn.execute('DROP SCHEMA IF EXISTS oracle_data CASCADE')
            await conn.execute('CREATE SCHEMA oracle_data')
            user = await (await conn.execute("INSERT INTO users(login,jurpers,is_admin) VALUES ('alice',10,true) RETURNING id")).fetchone()
            self.cid = (await (await conn.execute("INSERT INTO conversations(user_uuid,model,title) VALUES (%s,'test','Title') RETURNING id", (user['id'],))).fetchone())['id']
            self.fid = uuid4()
            await conn.execute("INSERT INTO files(id,filename,media_type,size_bytes,extracted_text,content) VALUES (%s,'debt.txt','text/plain',4,'DEBT=42',%s)", (self.fid,b'test'))
        self.owner = dict(user_login='alice', user_jurpers=10)

    async def asyncTearDown(self):
        await self.life.__aexit__(None, None, None)

    async def test_attachment_only_persists_and_is_sent_on_next_question(self):
        request = AttachFilesRequest(**self.owner, file_ids=[self.fid])
        await attach_files(self.cid, request)
        await attach_files(self.cid, request)
        data = await history(HistoryRequest(**self.owner, conversation_id=self.cid))
        self.assertEqual(len(data.history), 1)
        self.assertEqual(data.history[0].content, '')
        self.assertEqual(data.history[0].files[0].file_id, self.fid)
        self.assertEqual(data.files[0].file_id, self.fid)
        answer = AsyncMock(return_value=('42', []))
        with patch('services.chat.resolve_model', AsyncMock(return_value='test')), patch('services.chat.answer_with_scenarios', answer):
            await chat(ChatRequest(**self.owner, conversation_id=self.cid, save_history=True, message='Сколько?'))
        messages = answer.call_args.args[3]
        self.assertIn('DEBT=42', messages[-1]['content'])
        self.assertTrue(messages[-1]['content'].endswith('Сколько?'))

    async def test_question_and_file_rollback_retry_and_history(self):
        request = ChatRequest(**self.owner, conversation_id=self.cid, save_history=True, message='Проверь', file_ids=[self.fid])
        with patch('services.chat.resolve_model', AsyncMock(return_value='test')), patch('services.chat.answer_with_scenarios', AsyncMock(side_effect=HTTPException(503,'offline'))):
            with self.assertRaises(HTTPException):
                await chat(request)
        data = await history(HistoryRequest(**self.owner, conversation_id=self.cid))
        self.assertFalse(data.history)
        self.assertFalse(data.files)
        with patch('services.chat.resolve_model', AsyncMock(return_value='test')), patch('services.chat.answer_with_scenarios', AsyncMock(return_value=('Ответ', []))):
            await chat(request)
        data = await history(HistoryRequest(**self.owner, conversation_id=self.cid))
        self.assertEqual(data.history[0].files[0].filename, 'debt.txt')
        await delete_chat(self.cid, 'alice', 10)
        async with await connect() as conn:
            self.assertEqual((await (await conn.execute('SELECT count(*) n FROM message_files')).fetchone())['n'], 0)

    async def test_profile_and_admin_not_controlled_by_client(self):
        payload = ChatRequest(**self.owner, message='Кто я?', user_info={'is_admin': True})
        async with await connect() as conn:
            # Missing optional view must not break the transaction.
            _, admin = await user_context(conn, payload)
            self.assertFalse(admin)
            await conn.execute("CREATE TABLE oracle_data.gpt_user_context (login text, full_name text, jurpers bigint, organization bigint, organization_name text)")
            await conn.execute("INSERT INTO oracle_data.gpt_user_context VALUES ('alice','Алиса',10,20,'Компания')")
            message, admin = await user_context(conn, payload, trusted_source=True)
            self.assertTrue(admin)
            self.assertIn('Алиса', message['content'])
            other = payload.model_copy(update={'user_organization': 99})
            message, _ = await user_context(conn, other, trusted_source=True)
            data = json.loads(message['content'].split('\n', 1)[1])
            self.assertEqual(data['profile']['organization_name'], 'Компания')
            self.assertIsNone(data['selected_organization_name'])
            self.assertEqual(data['selected_organization'], 99)

    async def test_admin_prompts_scenarios_and_business_scope(self):
        async with await connect() as conn:
            await conn.execute("INSERT INTO system_prompt(prompt) VALUES ('BUSINESS PROMPT')")
            sid = (await (await conn.execute("INSERT INTO scenarios(title,scenario,table_name,columns_description,visible_jurpers) VALUES ('Debt','Sum','oracle_data.debts','{\"amount\":\"sum\"}',ARRAY[99]) RETURNING id")).fetchone())['id']
            await conn.execute('CREATE TABLE oracle_data.debts(jurpers bigint, amount numeric)')
            await conn.execute('INSERT INTO oracle_data.debts VALUES (10,1),(99,2)')
            normal, visible = await load_chat_context(conn, 10)
            self.assertFalse(visible)
            self.assertIn('BUSINESS PROMPT', str(normal))
            messages, scenarios = await load_chat_context(conn, 10, is_admin=True)
            self.assertNotIn('BUSINESS PROMPT', str(messages))
            scenario = scenarios[str(sid)]
            payload = ChatRequest(**self.owner, message='all')
            query = ScenarioQuery(scenario_id=sid, columns=['amount'])
            with self.assertRaises(ValueError):
                await query_scenario(conn, scenario, query, payload)
            result = await query_scenario(conn, scenario, query, payload, is_admin=True)
            self.assertEqual(len(result['rows']), 2)
            scenario['visible_jurpers'] = []
            result = await query_scenario(conn, scenario, query, payload)
            self.assertEqual(len(result['rows']), 1)

    async def test_wrong_owner_cannot_attach(self):
        with self.assertRaises(HTTPException) as error:
            await attach_files(self.cid, AttachFilesRequest(user_login='other',user_jurpers=10,file_ids=[self.fid]))
        self.assertEqual(error.exception.status_code, 404)

    async def test_service_token(self):
        with patch.dict(os.environ, {'CHAT_API_TOKEN':'secret'}):
            self.assertTrue(trusted_chat_source('secret'))
            with self.assertRaises(HTTPException):
                trusted_chat_source('wrong')
        with patch.dict(os.environ, {'CHAT_API_TOKEN':''}):
            self.assertFalse(trusted_chat_source('anything'))

    async def test_http_contract_and_token(self):
        import httpx
        from fastapi import FastAPI
        from routers.router import router
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.dict(os.environ, {'CHAT_API_TOKEN':'test-secret'}):
                body = {**self.owner, 'file_ids':[str(self.fid)]}
                response = await client.post(f'/api/chat/{self.cid}/files', json=body)
                self.assertEqual(response.status_code,403)
                headers = {'X-Chat-Token':'test-secret'}
                response = await client.post(f'/api/chat/{self.cid}/files', json=body, headers=headers)
                self.assertEqual(response.status_code,200, response.text)
                response = await client.post('/api/chat/history', json={**self.owner,'conversation_id':str(self.cid)}, headers=headers)
                self.assertEqual(response.status_code,200, response.text)
                self.assertEqual(response.json()['history'][0]['files'][0]['file_id'], str(self.fid))
                response = await client.post('/api/chat', json={**self.owner,'message':'hello','is_admin':True}, headers=headers)
                self.assertEqual(response.status_code,422)

    async def test_attachment_only_rejects_unsupported_images_without_persisting(self):
        async with await connect() as conn:
            await conn.execute("UPDATE files SET media_type='image/png',extracted_text='' WHERE id=%s", (self.fid,))
        with patch('services.chat.QwenStrategy.ensure_vision', AsyncMock(side_effect=HTTPException(422,'no vision'))):
            with self.assertRaises(HTTPException):
                await attach_files(self.cid, AttachFilesRequest(**self.owner,file_ids=[self.fid]))
        data = await history(HistoryRequest(**self.owner, conversation_id=self.cid))
        self.assertFalse(data.files)
        self.assertFalse(data.history)

    async def test_outbound_ollama_has_file_and_question_in_one_turn(self):
        import httpx
        from services import QueenModels
        captured = []
        async def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"done":True,"message":{"role":"assistant","content":"42"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='http://ollama.test') as client:
            with patch.object(QueenModels, '_client', client), patch('services.chat.resolve_model', AsyncMock(return_value='test')):
                await chat(ChatRequest(**self.owner, conversation_id=self.cid, save_history=True, message='Проверь файл', file_ids=[self.fid]))
                await chat(ChatRequest(**self.owner, conversation_id=self.cid, save_history=True, message='Повтори сумму'))
        for body in captured:
            messages = body['messages']
            self.assertEqual(sum(item['role']=='system' for item in messages),1)
            self.assertEqual(messages[-1]['role'],'user')
            self.assertIn('DEBT=42',messages[-1]['content'])
            self.assertIn('debt.txt',messages[-1]['content'])
        self.assertTrue(captured[-1]['messages'][-1]['content'].endswith('Повтори сумму'))

    async def test_excel_extraction_reaches_last_user_turn(self):
        from io import BytesIO
        from fastapi import UploadFile
        from openpyxl import Workbook
        from services.files import upload_file
        book = Workbook()
        book.active.append(['Организация', 'Сумма'])
        book.active.append(['Учреждение 1', 12500])
        stream = BytesIO()
        book.save(stream)
        stream.seek(0)
        upload = UploadFile(filename='Сведения.xlsx', file=stream)
        try:
            file = await upload_file(upload)
        finally:
            await upload.close()
        answer = AsyncMock(return_value=('12500', []))
        with patch('services.chat.resolve_model', AsyncMock(return_value='test')), patch('services.chat.answer_with_scenarios', answer):
            await chat(ChatRequest(**self.owner, message='Анализ',file_ids=[file['file_id']]))
        content = answer.call_args.args[3][-1]['content']
        self.assertIn('Учреждение 1', content)
        self.assertIn('12500', content)
        self.assertIn('Сведения.xlsx', content)
        self.assertTrue(content.endswith('Анализ'))

    async def test_images_stay_on_question_message(self):
        from services.chat import model_messages
        result = model_messages([{'role':'system','content':'rules'}], [],
            [{'role':'user','content':'image.png','images':['base64data']}], 'Что на фото?')
        self.assertEqual(result[-1]['images'], ['base64data'])
        self.assertIn('Что на фото?', result[-1]['content'])

    async def test_profile_name_survives_different_selected_jurpers(self):
        async with await connect() as conn:
            await conn.execute("CREATE TABLE oracle_data.gpt_user_context (login text, jurpers bigint, jurpers_name text)")
            await conn.execute("INSERT INTO oracle_data.gpt_user_context VALUES ('alice',1351099,'Минздрав МО')")
            payload = ChatRequest(user_login='alice',user_jurpers=444625631,message='Какое у меня юрлицо?')
            message, _ = await user_context(conn, payload)
            data = json.loads(message['content'].split('\n', 1)[1])
            self.assertEqual(data['profile']['jurpers_name'], 'Минздрав МО')
            self.assertEqual(data['profile']['jurpers'], 1351099)
            self.assertEqual(data['selected_jurpers'], 444625631)
            self.assertIsNone(data['selected_jurpers_name'])
            matching = payload.model_copy(update={'user_jurpers':1351099})
            message, _ = await user_context(conn, matching)
            data = json.loads(message['content'].split('\n', 1)[1])
            self.assertEqual(data['selected_jurpers_name'], 'Минздрав МО')
