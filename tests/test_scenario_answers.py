import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from fastapi import HTTPException
from config import settings
from schemas.qwen import ChatRequest
from services.scenario_runner import answer_with_scenarios


def message(status="completed", response="Ответ"):
    return {"role": "assistant", "content": json.dumps({"status": status, "response": response}, ensure_ascii=False)}


def report_call():
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "create_report", "arguments": {
            "format": "xlsx", "title": "Report", "columns": ["Total"], "rows": [[42]]}}}]}


class ScenarioAnswerTests(IsolatedAsyncioTestCase):
    def payload(self):
        return ChatRequest(user_login="alice", user_jurpers=10, message="Дай отчёт", save_history=False)

    async def run_replies(self, reply):
        with patch("services.scenario_runner.QwenStrategy.chat_message", reply):
            return await answer_with_scenarios(None, "test", self.payload(),
                                               [{"role": "user", "content": "Дай отчёт"}], {})

    async def test_completed_and_clarification_return_only_text(self):
        for status, text in [("completed", "**Итого:** 42 млн руб.\nГотово."),
                             ("needs_clarification", "За какой период нужен анализ?")]:
            reply = AsyncMock(return_value=message(status, text))
            answer, files = await self.run_replies(reply)
            self.assertEqual(answer, text)
            self.assertEqual(files, [])
            self.assertEqual(reply.await_count, 1)

    async def test_invalid_json_and_schema_repaired(self):
        for content in [" ", "Ответ", '{"status":"completed","response":"обрыв',
                        '{"response":"Нет статуса"}', '{"status":"done","response":"ok"}',
                        '{"status":"completed","response":42}', '{"status":"completed","response":"   "}',
                        '{"status":"completed","response":"ok","extra":true}']:
            reply = AsyncMock(side_effect=[{"content": content}, message()])
            answer, _ = await self.run_replies(reply)
            self.assertEqual(answer, "Ответ")
            self.assertEqual(reply.await_count, 2)

    async def test_repeated_invalid_json_is_bounded(self):
        reply = AsyncMock(return_value={"content": "broken"})
        with self.assertRaises(HTTPException) as error:
            await self.run_replies(reply)
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(reply.await_count, settings.MAX_ANSWER_REPAIRS + 1)

    async def test_processing_continues_without_confirmation(self):
        reply = AsyncMock(side_effect=[message("processing", "Анализирую"), message(response="Итого: 42")])
        answer, _ = await self.run_replies(reply)
        self.assertEqual(answer, "Итого: 42")
        self.assertEqual(reply.await_count, 2)

    async def test_identical_processing_stops_without_progress(self):
        reply = AsyncMock(return_value=message("processing", "Анализирую"))
        with self.assertRaises(HTTPException):
            await self.run_replies(reply)
        self.assertEqual(reply.await_count, 2)

    async def test_different_processing_messages_are_also_bounded(self):
        reply = AsyncMock(side_effect=[message("processing", f"Шаг {i}") for i in range(settings.MAX_ANSWER_CONTINUATIONS+1)])
        with self.assertRaises(HTTPException):
            await self.run_replies(reply)
        self.assertEqual(reply.await_count, settings.MAX_ANSWER_CONTINUATIONS+1)

    async def test_tool_results_survive_processing_and_format_repair(self):
        report = SimpleNamespace(filename="report.xlsx", download_url="/api/reports/example/download",
                                 model_dump=lambda **kwargs: {"download_url": "/api/reports/example/download"})
        reply = AsyncMock(side_effect=[
            message("processing", "Сформирую файл"), report_call(),
            {"content": "Неправильный формат"}, message(response="Готово")])
        create = AsyncMock(return_value=report)
        with patch("services.scenario_runner.create_report", create):
            answer, files = await self.run_replies(reply)
        self.assertIn(report.download_url, answer)
        self.assertNotIn("Неправильный", answer)
        self.assertEqual(files, [report])
        create.assert_awaited_once()

    async def test_generated_files_return_if_text_cannot_be_repaired(self):
        report = SimpleNamespace(filename="report.xlsx", download_url="/api/reports/example/download",
                                 model_dump=lambda **kwargs: {"download_url": "/api/reports/example/download"})
        reply = AsyncMock(side_effect=[report_call()] + [{"content": ""}]*(settings.MAX_ANSWER_REPAIRS+1))
        with patch("services.scenario_runner.create_report", AsyncMock(return_value=report)):
            answer, files = await self.run_replies(reply)
        self.assertIn(report.download_url, answer)
        self.assertIn("не смогла завершить", answer)
        self.assertEqual(files, [report])

    async def test_length_finish_reason_rejects_even_valid_json_and_tools(self):
        truncated = {**message(), "_done_reason": "length", "tool_calls": report_call()["tool_calls"]}
        reply = AsyncMock(side_effect=[truncated, message(response="Полный ответ")])
        create = AsyncMock()
        with patch("services.scenario_runner.create_report", create):
            answer, _ = await self.run_replies(reply)
        create.assert_not_awaited()
        self.assertEqual(answer, "Полный ответ")
        self.assertEqual(reply.await_count, 2)
