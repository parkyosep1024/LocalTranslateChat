import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from script.config.settings import PROJECT_ROOT
from script.config.translation_settings import (
    DEFAULT_CHUNK_MAX_CHARS,
    INPUT_DIR,
    OUTPUT_DIR,
    TranslationSettings,
)
from script.providers.local_llm import LocalLLM
from script.translate_main import run_translation
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.translator import Translator, split_text_into_chunks
from script.utils.exceptions import (
    ConfigurationError,
    FileProcessingError,
    InvalidResponseError,
    LocalLLMError,
)


class FakeResponse:
    def __init__(self, body: dict | bytes) -> None:
        self.body = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class EchoLocalLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        source = prompt.split("[원문]\n", 1)[1]
        return source.upper()


class SettingsTests(unittest.TestCase):
    def test_translation_paths_use_project_root(self) -> None:
        self.assertEqual(INPUT_DIR, PROJECT_ROOT / "setting" / "input")
        self.assertEqual(OUTPUT_DIR, PROJECT_ROOT / "setting" / "output")

    def test_reads_ollama_settings_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OLLAMA_MODEL=test-model\n"
                "OLLAMA_BASE_URL=http://127.0.0.1:1234\n"
                "OLLAMA_TIMEOUT=12.5\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = TranslationSettings.from_env(env_file)

        self.assertEqual(settings.model, "test-model")
        self.assertEqual(settings.base_url, "http://127.0.0.1:1234")
        self.assertEqual(settings.timeout, 12.5)

    def test_missing_model_is_reported(self) -> None:
        settings = TranslationSettings(model="")

        with self.assertRaisesRegex(ConfigurationError, "OLLAMA_MODEL"):
            settings.validate()


class FileTests(unittest.TestCase):
    def test_folders_are_created_and_only_top_level_txt_is_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            loader = FileLoader(input_dir)
            writer = FileWriter(output_dir)

            loader.ensure_input_dir()
            writer.ensure_output_dir()
            (input_dir / "003.txt").write_text("3", encoding="utf-8")
            (input_dir / "001.TXT").write_text("1", encoding="utf-8")
            (input_dir / "002.txt").write_text("2", encoding="utf-8")
            (input_dir / "image.png").write_text("x", encoding="utf-8")
            nested = input_dir / "nested"
            nested.mkdir()
            (nested / "000.txt").write_text("0", encoding="utf-8")

            files = loader.list_txt_files()

            self.assertTrue(input_dir.is_dir())
            self.assertTrue(output_dir.is_dir())
            self.assertEqual([path.name for path in files], ["001.TXT", "002.txt", "003.txt"])

    def test_invalid_utf8_read_error_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.txt"
            path.write_bytes(b"\xff\xfe\xfa")

            with self.assertRaisesRegex(FileProcessingError, "UTF-8"):
                FileLoader.read_text(path)

    def test_writer_saves_korean_utf8_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input" / "대사.txt"
            input_path.parent.mkdir()
            input_path.write_text("원문", encoding="utf-8")
            writer = FileWriter(root / "output")

            output_path = writer.write(input_path, "번역된 한글\r\n둘째 줄")

            self.assertEqual(output_path.name, input_path.name)
            self.assertEqual(
                output_path.read_bytes(), "번역된 한글\r\n둘째 줄".encode("utf-8")
            )
            self.assertFalse((root / "output" / "대사.txt.tmp").exists())


class ChunkTests(unittest.TestCase):
    def test_chunks_prefer_line_boundaries_and_keep_order(self) -> None:
        text = "1111\n2222\n3333"
        chunks = split_text_into_chunks(text, max_chars=6)

        self.assertEqual(chunks, ["1111\n", "2222\n", "3333"])
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 6 for chunk in chunks))

    def test_single_long_line_is_split_at_maximum_size(self) -> None:
        chunks = split_text_into_chunks("abcdefghij", max_chars=4)

        self.assertEqual(chunks, ["abcd", "efgh", "ij"])
        self.assertTrue(all(chunks))

    def test_empty_text_does_not_create_empty_chunk(self) -> None:
        self.assertEqual(split_text_into_chunks("", DEFAULT_CHUNK_MAX_CHARS), [])


class LocalLLMTests(unittest.TestCase):
    def test_sends_generate_request_and_preserves_response_whitespace(self) -> None:
        captured: dict = {}

        def opener(request, **kwargs):
            captured["url"] = request.full_url
            captured["timeout"] = kwargs["timeout"]
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"response": " 번역 결과\n"})

        settings = TranslationSettings(
            model="test-model",
            base_url="http://127.0.0.1:11434",
            timeout=15,
        )
        result = LocalLLM(settings, opener=opener).generate("번역 프롬프트")

        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/generate")
        self.assertEqual(captured["timeout"], 15)
        self.assertEqual(
            captured["body"],
            {"model": "test-model", "prompt": "번역 프롬프트", "stream": False},
        )
        self.assertEqual(result, " 번역 결과\n")

    def test_connection_failure_is_reported(self) -> None:
        def opener(*_args, **_kwargs):
            raise URLError("connection refused")

        client = LocalLLM(TranslationSettings(model="test-model"), opener=opener)

        with self.assertRaisesRegex(LocalLLMError, "연결할 수 없습니다"):
            client.generate("prompt")

    def test_timeout_is_reported(self) -> None:
        def opener(*_args, **_kwargs):
            raise socket.timeout("timed out")

        client = LocalLLM(TranslationSettings(model="test-model"), opener=opener)

        with self.assertRaisesRegex(LocalLLMError, "시간 초과"):
            client.generate("prompt")

    def test_missing_model_http_error_is_reported(self) -> None:
        def opener(*_args, **_kwargs):
            raise HTTPError(
                "http://localhost:11434/api/generate",
                404,
                "Not Found",
                None,
                io.BytesIO(b'{"error":"model test-model not found"}'),
            )

        client = LocalLLM(TranslationSettings(model="test-model"), opener=opener)

        with self.assertRaisesRegex(LocalLLMError, "모델을 찾을 수 없습니다"):
            client.generate("prompt")

    def test_invalid_json_is_reported(self) -> None:
        client = LocalLLM(
            TranslationSettings(model="test-model"),
            opener=lambda *_args, **_kwargs: FakeResponse(b"not-json"),
        )

        with self.assertRaisesRegex(InvalidResponseError, "JSON"):
            client.generate("prompt")

    def test_missing_or_empty_response_is_reported(self) -> None:
        for data, message in [({}, "찾을 수 없습니다"), ({"response": "  "}, "빈 번역")]:
            with self.subTest(data=data):
                client = LocalLLM(
                    TranslationSettings(model="test-model"),
                    opener=lambda *_args, _data=data, **_kwargs: FakeResponse(_data),
                )
                with self.assertRaisesRegex(InvalidResponseError, message):
                    client.generate("prompt")


class TranslatorTests(unittest.TestCase):
    def test_translates_chunks_in_order_and_saves_same_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            source_path = input_dir / "dialogue.txt"
            original = "one\ntwo\nthree"
            source_path.write_text(original, encoding="utf-8")
            llm = EchoLocalLLM()
            translator = Translator(
                FileLoader(input_dir),
                llm,  # type: ignore[arg-type]
                FileWriter(output_dir),
                chunk_max_chars=5,
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertEqual(summary.succeeded, 1)
            self.assertGreater(len(llm.prompts), 1)
            self.assertEqual(
                (output_dir / "dialogue.txt").read_text(encoding="utf-8"),
                original.upper(),
            )
            self.assertEqual(source_path.read_text(encoding="utf-8"), original)

    def test_existing_output_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            source_path = input_dir / "same.txt"
            source_path.write_text("source", encoding="utf-8")
            output_path = output_dir / "same.txt"
            output_path.write_text("existing", encoding="utf-8")
            llm = EchoLocalLLM()
            translator = Translator(
                FileLoader(input_dir), llm, FileWriter(output_dir)  # type: ignore[arg-type]
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertEqual(summary.skipped, 1)
            self.assertEqual(llm.prompts, [])
            self.assertEqual(output_path.read_text(encoding="utf-8"), "existing")

    def test_failed_chunk_creates_no_output_file(self) -> None:
        class FailOnSecondChunk(EchoLocalLLM):
            def generate(self, prompt: str) -> str:
                if len(self.prompts) == 1:
                    raise LocalLLMError("두 번째 Chunk 실패")
                return super().generate(prompt)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "failed.txt").write_text("1111\n2222", encoding="utf-8")
            translator = Translator(
                FileLoader(input_dir),
                FailOnSecondChunk(),  # type: ignore[arg-type]
                FileWriter(output_dir),
                chunk_max_chars=5,
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertEqual(summary.failed, 1)
            self.assertFalse((output_dir / "failed.txt").exists())
            self.assertFalse((output_dir / "failed.txt.tmp").exists())

    def test_one_file_failure_does_not_stop_next_file(self) -> None:
        class FailByContent(EchoLocalLLM):
            def generate(self, prompt: str) -> str:
                if "FAIL" in prompt:
                    raise LocalLLMError("테스트 실패")
                return super().generate(prompt)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "001.txt").write_text("FAIL", encoding="utf-8")
            (input_dir / "002.txt").write_text("ok", encoding="utf-8")
            translator = Translator(
                FileLoader(input_dir),
                FailByContent(),  # type: ignore[arg-type]
                FileWriter(output_dir),
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertEqual((summary.failed, summary.succeeded), (1, 1))
            self.assertFalse((output_dir / "001.txt").exists())
            self.assertEqual(
                (output_dir / "002.txt").read_text(encoding="utf-8"), "OK"
            )

    def test_file_read_failure_does_not_stop_next_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "001.txt").write_bytes(b"\xff\xfe")
            (input_dir / "002.txt").write_text("ok", encoding="utf-8")
            translator = Translator(
                FileLoader(input_dir),
                EchoLocalLLM(),  # type: ignore[arg-type]
                FileWriter(output_dir),
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertEqual((summary.failed, summary.succeeded), (1, 1))
            self.assertFalse((output_dir / "001.txt").exists())
            self.assertEqual(
                (output_dir / "002.txt").read_text(encoding="utf-8"), "OK"
            )

    def test_empty_input_folder_exits_normally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = TranslationSettings(
                model="test-model",
                input_dir=root / "input",
                output_dir=root / "output",
            )
            outputs: list[str] = []

            exit_code = run_translation(settings, output_func=outputs.append)

            self.assertEqual(exit_code, 0)
            self.assertTrue(settings.input_dir.is_dir())
            self.assertTrue(settings.output_dir.is_dir())
            self.assertIn("번역할 TXT/CSV/JSON 파일이 없습니다.", outputs)

    def test_entry_point_reports_missing_model(self) -> None:
        outputs: list[str] = []

        exit_code = run_translation(
            TranslationSettings(model=""), output_func=outputs.append
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            outputs,
            ["설정 오류: OLLAMA_MODEL을 .env 파일에 설정해 주세요."],
        )


if __name__ == "__main__":
    unittest.main()
