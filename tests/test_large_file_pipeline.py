"""Run with TEST_DATABASE=1 against a disposable DB; optional STRESS_FILE_PATH."""
import io
import json
import os
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch

import httpx
from fastapi import UploadFile
from database.history import connect, database_lifespan
from services.files import upload_file, file_context
from services.chat import model_messages
from services.scenario_runner import answer_with_scenarios
from schemas.qwen import ChatRequest


@skipUnless(os.getenv('TEST_DATABASE') == '1', 'requires disposable PostgreSQL')
class LargeFilePipelineTests(IsolatedAsyncioTestCase):
    async def test_full_text_survives_upload_database_context_and_http(self):
        source = os.getenv('STRESS_FILE_PATH')
        raw = Path(source).read_bytes() if source else (
            'CONTROL-A=FALCON-731\n' + 'обычная строка журнала\n' * 20000 + '\nEND-MARKER').encode()
        original = raw.decode('utf-8-sig')
        async with database_lifespan():
            async with await connect() as conn:
                await conn.execute(Path('/database/init.sql').read_text())
            record = await upload_file(UploadFile(filename='stress.txt', file=io.BytesIO(raw)))
            try:
                async with await connect() as conn:
                    stored = await (await conn.execute('SELECT extracted_text,content FROM files WHERE id=%s',
                                                       (record['file_id'],))).fetchone()
                    self.assertEqual(stored['extracted_text'], original)
                    self.assertEqual(bytes(stored['content']), raw)
                    context = await file_context(conn, [record['file_id']])
                    self.assertEqual(json.loads(context[-1]['content'].split('\n', 1)[1])[0]['text'], original)
                    messages = model_messages([{'role':'system','content':'Инструкции'}], [
                        {'role':'user','content':'Предыдущий вопрос'},
                        {'role':'assistant','content':'Предыдущий ответ'}], context, 'Проверь весь файл')
                    captured = []
                    def handle(request):
                        body = json.loads(request.content)
                        captured.append(body)
                        encoded = body['messages'][-1]['content'].split('\n', 1)[1].split('\n\nВопрос пользователя:', 1)[0]
                        self.assertEqual(json.loads(encoded)[0]['text'], original)
                        self.assertFalse(body['truncate'])
                        self.assertEqual([m['role'] for m in body['messages']], ['system','user','assistant','user'])
                        return httpx.Response(200, json={'done':True,'done_reason':'stop','message':{
                            'content':json.dumps({'status':'completed','response':'ok'})}})
                    async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
                        with patch('services.QueenModels._client', client):
                            await answer_with_scenarios(conn, 'test', ChatRequest(user_login='audit',
                                user_jurpers=1, message='Проверь весь файл'), messages, {})
                    self.assertEqual(len(captured), 1)
            finally:
                async with await connect() as conn:
                    await conn.execute('DELETE FROM files WHERE id=%s', (record['file_id'],))
