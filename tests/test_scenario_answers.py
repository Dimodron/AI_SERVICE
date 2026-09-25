from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from fastapi import HTTPException
from schemas.qwen import ChatRequest
from services.scenario_runner import answer_with_scenarios


class ScenarioAnswerTests(IsolatedAsyncioTestCase):
    def payload(self):
        return ChatRequest(user_login='alice', user_jurpers=10, message='Дай отчёт', save_history=False)

    async def test_empty_answer_retried_once(self):
        reply = AsyncMock(side_effect=[{'content': ' '}, {'content': 'Ответ'}])
        with patch('services.scenario_runner.QwenStrategy.chat_message', reply):
            answer, files = await answer_with_scenarios(None, 'test', self.payload(), [{'role':'user','content':'Дай отчёт'}], {})
        self.assertEqual(answer, 'Ответ')
        self.assertEqual(files, [])
        self.assertEqual(reply.await_count, 2)

    async def test_repeated_empty_answer_is_error(self):
        reply = AsyncMock(return_value={'content': ''})
        with patch('services.scenario_runner.QwenStrategy.chat_message', reply):
            with self.assertRaises(HTTPException) as error:
                await answer_with_scenarios(None, 'test', self.payload(), [], {})
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(reply.await_count, 2)

    async def test_generated_file_survives_empty_final_text(self):
        report = SimpleNamespace(filename='report.xlsx', download_url='/api/reports/example/download',
                                 model_dump=lambda **kwargs: {'download_url':'/api/reports/example/download'})
        reply = AsyncMock(side_effect=[{'role':'assistant','content':'','tool_calls':[
            {'function':{'name':'create_report','arguments':{'format':'xlsx','title':'Report','columns':['Total'],'rows':[[42]]}}}]},
            {'content':''}])
        with patch('services.scenario_runner.QwenStrategy.chat_message', reply), patch('services.scenario_runner.create_report', AsyncMock(return_value=report)):
            answer, files = await answer_with_scenarios(None, 'test', self.payload(), [], {})
        self.assertIn(report.download_url, answer)
        self.assertEqual(files, [report])
        self.assertEqual(reply.await_count, 2)
