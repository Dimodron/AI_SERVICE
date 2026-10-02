import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx

from config import settings
from schemas.qwen import GenerationSettings, ModelOptions
from services.context_diagnostics import request_metrics
from services.QueenModels import QwenStrategy


class DiagnosticsTests(IsolatedAsyncioTestCase):
    async def test_http_body_is_complete_and_truncation_disabled(self):
        text = 'CONTROL-A=FALCON-731\n' + 'данные\n' * 60000 + '\nEND-MARKER'
        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'done': True, 'done_reason': 'stop',
                'prompt_eval_count': 123, 'message': {'content': 'ok'}})
        diagnostic_settings = settings.model_copy(update={
            'CONTEXT_DIAGNOSTICS': True, 'CONTEXT_DIAGNOSTIC_MARKERS': ['CONTROL-A', 'END-MARKER']})
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
            with patch('services.QueenModels._client', client), patch('services.QueenModels.settings', diagnostic_settings):
                with self.assertLogs('services.context_diagnostics', level='WARNING') as logs:
                    await QwenStrategy('test', GenerationSettings(options=ModelOptions(num_ctx=8192))).chat_message([
                        {'role': 'system', 'content': 'instructions'}, {'role': 'user', 'content': text}])
        self.assertFalse(sent[0]['truncate'])
        self.assertEqual(sent[0]['messages'][-1]['content'], text)
        metrics = request_metrics(sent[0], ['CONTROL-A', 'END-MARKER'])
        self.assertEqual(metrics['configured_num_ctx'], 8192)
        self.assertTrue(all(metrics['markers'].values()))
        self.assertGreater(metrics['prompt_bytes'], metrics['prompt_chars'])
        self.assertNotIn('FALCON-731', '\n'.join(logs.output))
        self.assertIn('prompt_eval_count', '\n'.join(logs.output))

    async def test_overflow_is_not_retried_with_silent_truncation(self):
        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(400, json={'error': 'request exceeds the available context size'})
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
            with patch('services.QueenModels._client', client):
                with self.assertRaises(httpx.HTTPStatusError):
                    await QwenStrategy('test').chat_message([{'role':'user','content':'large document'}])
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]['truncate'])

    async def test_nested_ollama_overflow_has_clear_user_message(self):
        from services.errors import service_error
        from starlette.requests import Request
        request = httpx.Request('POST', 'http://test/api/chat')
        response = httpx.Response(400, request=request, json={'error': json.dumps({'error':{
            'message':'request (239069 tokens) exceeds the available context size (50432 tokens), try increasing it'}})})
        error = httpx.HTTPStatusError('overflow', request=request, response=response)
        result = await service_error(Request({'type':'http','method':'POST','path':'/api/chat','headers':[]}), error)
        data = json.loads(result.body)
        self.assertEqual(result.status_code, 422)
        self.assertIn('молчаливое усечение отключено', data['detail'])
        self.assertIn('239069', data['detail'])
