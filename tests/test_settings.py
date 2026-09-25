import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pydantic import ValidationError
from config import DEFAULT_SETTINGS_PATH, load_settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = load_settings(overrides={})
        self.assertEqual(settings.MAX_FILE_BYTES, 5242880)
        self.assertEqual(settings.MAX_FILES, 5)

    def test_legacy_environment_override(self):
        settings = load_settings(overrides={'MAX_FILES': '8', 'MAX_FILE_BYTES': '', 'QWEN_URL': 'http://example.test:11434'})
        self.assertEqual(settings.MAX_FILES, 8)
        self.assertEqual(settings.MAX_FILE_BYTES, 5242880)
        self.assertEqual(settings.QWEN_URL, 'http://example.test:11434')

    def test_rejects_invalid_limits_and_unknown_keys(self):
        original = DEFAULT_SETTINGS_PATH.read_text()
        for content in (original.replace('MAX_FILES = 5', 'MAX_FILES = 0'),
                        original.replace('MAX_FILES = 5', 'MAX_FILES = true'),
                        original + '\nMISSPELLED_SETTING = 5\n'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'settings.toml'
                path.write_text(content)
                with self.assertRaises(ValidationError):
                    load_settings(path, overrides={})

    def test_custom_settings_apply_to_schema_service_and_frontend_endpoint(self):
        content = DEFAULT_SETTINGS_PATH.read_text().replace('MAX_FILES = 5', 'MAX_FILES = 2').replace('MAX_FILE_BYTES = 5242880', 'MAX_FILE_BYTES = 4')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.toml'
            path.write_text(content)
            environment = {key: value for key, value in os.environ.items() if key not in {'MAX_FILES', 'MAX_FILE_BYTES', 'QWEN_URL', 'PG_POOL_SIZE', 'DATA_IMPORT_TIMEOUT'}}
            environment['SETTINGS_FILE'] = str(path)
            code = '''
import asyncio
import io
from uuid import uuid4
from unittest.mock import AsyncMock
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from routers.file_router import limits
from schemas.qwen import ChatRequest
from services.files import file_context, upload_file
async def verify():
    assert (await limits())["max_files"] == 2
    assert (await limits())["max_file_bytes"] == 4
    ids = [uuid4() for _ in range(3)]
    ChatRequest(message="test", user_login="test", user_jurpers=1, file_ids=ids[:2])
    try:
        ChatRequest(message="test", user_login="test", user_jurpers=1, file_ids=ids)
    except ValidationError:
        pass
    else:
        raise AssertionError("Request allowed too many attachments")
    # One old attachment plus two new files must exceed the per-chat limit.
    connection = AsyncMock()
    connection.execute.return_value.fetchall.return_value = [{"file_id": ids[0]}]
    try:
        await file_context(connection, ids[1:], uuid4())
    except HTTPException as exc:
        assert exc.status_code == 413 and '2 файлов' in exc.detail
    else:
        raise AssertionError("Combined chat limit ignored")
    upload = UploadFile(filename="test.txt", file=io.BytesIO(b"12345"))
    try:
        await upload_file(upload)
    except HTTPException as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("Upload size limit ignored")
    finally:
        await upload.close()
asyncio.run(verify())
'''
            subprocess.run([sys.executable, '-c', code], env=environment, check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()


class DefaultModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_default_and_explicit_selection(self):
        from unittest.mock import AsyncMock, patch
        from fastapi import HTTPException
        from services.QueenModels import resolve_model
        from config import settings
        with patch('services.QueenModels.list_models', AsyncMock(return_value=['another', settings.DEFAULT_MODEL])):
            self.assertEqual(await resolve_model(None), settings.DEFAULT_MODEL)
            self.assertEqual(await resolve_model('another'), 'another')
        with patch('services.QueenModels.list_models', AsyncMock(return_value=['another'])):
            with self.assertRaises(HTTPException) as error:
                await resolve_model(None)
            self.assertEqual(error.exception.status_code, 422)
