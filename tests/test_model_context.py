import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
import httpx
from config import settings
from schemas.qwen import GenerationSettings, ModelOptions
from services.QueenModels import QwenStrategy


class ContextTests(IsolatedAsyncioTestCase):
    async def test_default_empty_null_and_override(self):
        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'done':True,'message':{'role':'assistant','content':'ok'}})
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
            with patch('services.QueenModels._client',client):
                for options in (None, ModelOptions(), ModelOptions(num_ctx=None), ModelOptions(num_ctx=8192)):
                    await QwenStrategy('test',GenerationSettings(options=options,think=False)).chat_message([{'role':'user','content':'test'}])
        self.assertEqual([r['options']['num_ctx'] for r in sent], [settings.QWEN_NUM_CTX]*3+[8192])
        self.assertTrue(all(r['think'] is False and r['stream'] is False for r in sent))
