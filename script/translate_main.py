"""Ollama TXT/CSV/JSON 번역과 Prompt Preset 작성을 위한 콘솔 진입점입니다."""

from collections.abc import Callable
from pathlib import Path
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from script.config.settings import Settings
from script.config.translation_settings import TranslationSettings
from script.providers.gemini import GeminiClient
from script.providers.local_llm import LocalLLM
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.formats import FormatHandler, create_handler
from script.translation.language_detector import LanguageDetector, SUPPORTED_LANGUAGES
from script.translation.prompt_builder import GeminiPromptAI, PromptBuilder
from script.translation.prompt_manager import PromptManager, PromptPreset
from script.translation.translator import (
    BASIC_TRANSLATION_PROMPT,
    TranslationSummary,
    Translator,
)
from script.utils.exceptions import ChatbotError, ConfigurationError


InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


def create_translator(
    settings: TranslationSettings,
    source_language: str = "Unknown",
    target_language: str = "Korean",
    selected_prompt: str | None = None,
    prompt_name: str = "기본 Prompt",
    stop_requested: Callable[[], bool] | None = None,
    handlers: dict[Path, FormatHandler] | None = None,
    file_paths: list[Path] | None = None,
) -> Translator:
    return Translator(
        loader=FileLoader(settings.input_dir),
        local_llm=LocalLLM(settings),
        writer=FileWriter(settings.output_dir),
        chunk_max_chars=settings.chunk_max_chars,
        source_language=source_language,
        target_language=target_language,
        selected_prompt=selected_prompt,
        prompt_name=prompt_name,
        stop_requested=stop_requested,
        handlers=handlers,
        file_paths=file_paths,
    )


def run_translation(
    settings: TranslationSettings | None = None,
    output_func: OutputFunction = print,
) -> int:
    """2주차 방식의 기본 한국어 번역 흐름을 그대로 제공합니다."""

    try:
        settings = settings or TranslationSettings.from_env()
        settings.validate()
    except ConfigurationError as error:
        output_func(f"설정 오류: {error}")
        return 1

    _display_translation_header(settings, output_func)
    translator = create_translator(
        settings, file_paths=FileLoader(settings.input_dir).list_txt_files()
    )
    summary = translator.translate_all(output_func)

    if summary.total == 0:
        _display_empty_input(output_func)
        return 0

    _display_counts(summary, output_func)
    return 1 if summary.failed else 0


def choose_language(
    title: str,
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> str | None:
    output_func(title)
    for number, language in SUPPORTED_LANGUAGES.items():
        output_func(f"{number}. {language}")
    output_func("0. 취소")

    choice = _read_choice(set(SUPPORTED_LANGUAGES) | {"0"}, input_func, output_func)
    return None if choice in {None, "0"} else SUPPORTED_LANGUAGES[choice]


def confirm_source_language(
    detected_language: str,
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> str | None:
    while True:
        output_func(f"자동 감지된 원본 언어: {detected_language}")
        output_func("1. 그대로 사용")
        output_func("2. 직접 변경")
        output_func("3. 취소")
        choice = _read_choice({"1", "2", "3"}, input_func, output_func)
        if choice in {None, "3"}:
            return None
        if choice == "1":
            return detected_language
        selected = choose_language("원본 언어를 선택하세요.", input_func, output_func)
        if selected is not None:
            return selected


def run_interactive_translation(
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> None:
    try:
        settings = TranslationSettings.from_env()
        settings.validate()
    except ConfigurationError as error:
        output_func(f"설정 오류: {error}")
        return

    loader = FileLoader(settings.input_dir)
    writer = FileWriter(settings.output_dir)
    input_files = loader.list_supported_files()
    writer.ensure_output_dir()
    if not input_files:
        _display_empty_input(output_func)
        return

    prepared = _prepare_handlers(input_files, writer, settings.chunk_max_chars, input_func, output_func)
    if prepared is None:
        output_func("번역을 취소했습니다.")
        return
    handlers, samples = prepared
    detected = LanguageDetector.detect_text(samples)
    source_language = confirm_source_language(detected, input_func, output_func)
    if source_language is None:
        output_func("번역을 취소했습니다.")
        return

    target_language = choose_language(
        "목표 언어를 선택하세요.", input_func, output_func
    )
    if target_language is None:
        output_func("번역을 취소했습니다.")
        return

    manager = PromptManager()
    selection = _select_and_confirm_prompt(
        manager,
        source_language,
        target_language,
        input_func,
        output_func,
    )
    if selection is None:
        output_func("번역을 취소했습니다.")
        return
    selected_prompt, prompt_name = selection

    _display_translation_header(settings, output_func)
    stop_requested = _create_stop_checker(input_func, output_func)
    translator = create_translator(
        settings,
        source_language=source_language,
        target_language=target_language,
        selected_prompt=selected_prompt,
        prompt_name=prompt_name,
        stop_requested=stop_requested,
        handlers=handlers,
    )
    summary = translator.translate_all(output_func)

    output_func("")
    output_func("번역 중지" if summary.was_stopped else "번역 완료")
    output_func("")
    output_func(f"원본 언어: {source_language}")
    output_func(f"목표 언어: {target_language}")
    output_func(f"사용 Prompt: {prompt_name}")
    _display_counts(summary, output_func)
    output_func("")
    output_func(f"결과 폴더: {settings.output_dir}")


def run_prompt_creation(
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> None:
    try:
        translation_settings = TranslationSettings.from_env()
    except ConfigurationError as error:
        output_func(f"설정 오류: {error}")
        return
    loader = FileLoader(translation_settings.input_dir)
    input_files = loader.list_supported_files()
    if not input_files:
        _display_empty_input(output_func)
        return

    prepared = _prepare_handlers(
        input_files, None, translation_settings.chunk_max_chars, input_func, output_func
    )
    if prepared is None:
        output_func("Prompt 작성을 취소했습니다.")
        return
    _, samples = prepared
    detected = LanguageDetector.detect_text(samples)
    source_language = confirm_source_language(detected, input_func, output_func)
    if source_language is None:
        output_func("Prompt 작성을 취소했습니다.")
        return
    target_language = choose_language(
        "목표 언어를 선택하세요.", input_func, output_func
    )
    if target_language is None:
        output_func("Prompt 작성을 취소했습니다.")
        return

    try:
        document_type = input_func(
            "사용 목적 또는 문서 유형을 입력하세요 (예: game_dialogue): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        output_func("Prompt 작성을 취소했습니다.")
        return
    if not document_type:
        document_type = "general"

    try:
        gemini_settings = Settings.from_env()
        gemini_settings.validate()
        prompt_ai = GeminiPromptAI(GeminiClient(gemini_settings))
        builder = PromptBuilder(prompt_ai)
        draft = builder.create_draft(
            input_files, source_language, target_language, document_type,
            sample_text=samples,
        )
    except ChatbotError as error:
        output_func(f"Prompt 생성 실패: {error}")
        return

    manager = PromptManager()
    while True:
        _display_prompt("AI가 다음 번역 Prompt를 생성했습니다.", draft, output_func)
        output_func("1. 이 Prompt 저장")
        output_func("2. 수정 요청")
        output_func("3. 취소")
        choice = _read_choice({"1", "2", "3"}, input_func, output_func)

        if choice in {None, "3"}:
            output_func("Prompt 작성을 취소했습니다.")
            return
        if choice == "2":
            try:
                revision = input_func("수정하고 싶은 내용을 입력하세요: ").strip()
            except (EOFError, KeyboardInterrupt):
                output_func("Prompt 작성을 취소했습니다.")
                return
            if not revision:
                output_func("수정 요구사항을 입력해 주세요.")
                continue
            try:
                draft = builder.revise_draft(
                    draft,
                    revision,
                    input_files,
                    source_language,
                    target_language,
                    document_type,
                    sample_text=samples,
                )
            except (ChatbotError, ValueError) as error:
                output_func(f"Prompt 수정 실패: {error}")
            continue

        try:
            name = input_func("저장할 Preset 이름을 입력하세요: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_func("Prompt 작성을 취소했습니다.")
            return
        if not name:
            output_func("Preset 이름은 비워 둘 수 없습니다.")
            continue

        try:
            preset = manager.create_preset(
                name=name,
                source_language=source_language,
                target_language=target_language,
                document_type=document_type,
                prompt=draft,
                created_by="AI",
            )
            existing = manager.find_by_name(name)
            overwrite = False
            if existing is not None:
                output_func("같은 이름의 Preset이 이미 있습니다.")
                output_func("1. 기존 Preset 덮어쓰기")
                output_func("2. 취소")
                overwrite_choice = _read_choice(
                    {"1", "2"}, input_func, output_func
                )
                if overwrite_choice != "1":
                    output_func("Preset 저장을 취소했습니다.")
                    return
                overwrite = True
            path = manager.save(preset, overwrite=overwrite)
        except ChatbotError as error:
            output_func(f"Preset 저장 실패: {error}")
            continue

        output_func(f"Preset을 저장했습니다: {path}")
        return


def run_menu(
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> int:
    while True:
        output_func("Local Translate Chat")
        output_func("")
        output_func("1. 번역")
        output_func("2. Prompt 작성")
        output_func("3. 종료")
        choice = _read_choice({"1", "2", "3"}, input_func, output_func)

        if choice in {None, "3"}:
            output_func("프로그램을 종료합니다.")
            return 0
        if choice == "1":
            run_interactive_translation(input_func, output_func)
        else:
            run_prompt_creation(input_func, output_func)
        output_func("")


def _select_and_confirm_prompt(
    manager: PromptManager,
    source_language: str,
    target_language: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[str | None, str] | None:
    while True:
        try:
            presets = manager.list_presets()
        except ChatbotError as error:
            output_func(f"Preset 불러오기 실패: {error}")
            return None

        output_func("사용 가능한 Prompt Preset")
        output_func("1. 기본 Prompt")
        for index, preset in enumerate(presets, start=2):
            output_func(f"{index}. {preset.name}")
        output_func("0. 취소")

        valid = {str(index) for index in range(1, len(presets) + 2)} | {"0"}
        choice = _read_choice(valid, input_func, output_func)
        if choice in {None, "0"}:
            return None

        preset: PromptPreset | None = None
        if choice != "1":
            preset = presets[int(choice) - 2]
            if (
                preset.source_language != source_language
                or preset.target_language != target_language
            ):
                output_func("선택한 Prompt Preset의 언어 설정이 현재 번역 설정과 다릅니다.")
                output_func(
                    f"Preset: {preset.source_language} → {preset.target_language}"
                )
                output_func(f"현재: {source_language} → {target_language}")
                output_func("1. 그래도 사용")
                output_func("2. 다른 Preset 선택")
                output_func("3. 취소")
                mismatch_choice = _read_choice(
                    {"1", "2", "3"}, input_func, output_func
                )
                if mismatch_choice in {None, "3"}:
                    return None
                if mismatch_choice == "2":
                    continue

        prompt = (
            preset.prompt
            if preset is not None
            else BASIC_TRANSLATION_PROMPT.format(target_language=target_language).strip()
        )
        prompt_name = preset.name if preset is not None else "기본 Prompt"
        _display_prompt("현재 Prompt:", prompt, output_func, label="[Prompt]")
        output_func("1. 번역 시작")
        output_func("2. 다른 Prompt 선택")
        output_func("3. 취소")
        confirm = _read_choice({"1", "2", "3"}, input_func, output_func)
        if confirm == "1":
            return (preset.prompt if preset is not None else None, prompt_name)
        if confirm in {None, "3"}:
            return None


def _create_stop_checker(
    input_func: InputFunction,
    output_func: OutputFunction,
) -> Callable[[], bool]:
    def check() -> bool:
        try:
            answer = input_func(
                "계속하려면 Enter, 번역을 중지하려면 q를 입력하세요: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            output_func("")
            return True
        return answer in {"q", "quit", "/stop", "stop"}

    return check


def _read_choice(
    valid_choices: set[str],
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str | None:
    while True:
        try:
            choice = input_func("선택: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_func("")
            return None
        if choice in valid_choices:
            return choice
        output_func("올바른 번호를 입력해 주세요.")


def _display_translation_header(
    settings: TranslationSettings,
    output_func: OutputFunction,
) -> None:
    output_func("Local LLM TXT/CSV/JSON Translator")
    output_func("")
    output_func(f"입력 폴더: {settings.input_dir}")
    output_func(f"출력 폴더: {settings.output_dir}")
    output_func(f"사용 모델: {settings.model}")
    output_func("")


def _display_empty_input(output_func: OutputFunction) -> None:
    output_func("번역할 TXT/CSV/JSON 파일이 없습니다.")
    output_func("setting/input 폴더에 파일을 추가해 주세요.")


def _display_prompt(
    title: str,
    prompt: str,
    output_func: OutputFunction,
    label: str = "[Prompt Draft]",
) -> None:
    output_func(title)
    output_func("")
    output_func("--------------------------------")
    output_func(label)
    output_func(prompt)
    output_func("--------------------------------")
    output_func("")


def _display_counts(
    summary: TranslationSummary,
    output_func: OutputFunction,
) -> None:
    output_func("")
    output_func("--------------------------------")
    output_func(f"성공 {summary.succeeded}개")
    output_func(f"건너뜀 {summary.skipped}개")
    output_func(f"실패 {summary.failed}개")
    output_func(f"중지 {summary.stopped}개")
    for extension, label in ((".txt", "TXT"), (".csv", "CSV"), (".json", "JSON")):
        success, failed = summary.by_format.get(extension, (0, 0))
        output_func(f"{label} 성공: {success}, 실패: {failed}")


def _prepare_handlers(
    files: list[Path],
    writer: FileWriter | None,
    chunk_max_chars: int,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> tuple[dict[Path, FormatHandler], str] | None:
    """사용자가 선택한 셀/값만 샘플링하고 파일별 Handler를 준비합니다."""
    output_func("번역 가능한 파일을 발견했습니다.")
    for index, path in enumerate(files, 1):
        output_func(f"{index}. {path.name}")
    handlers: dict[Path, FormatHandler] = {}
    samples: list[str] = []
    remaining = 12_000
    field_cache: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for path in files:
        if writer is not None and writer.exists_for(path):
            continue
        try:
            handler = create_handler(path, chunk_max_chars)
            handler.load(path)
            fields = handler.available_fields()
            if path.suffix.lower() in {".csv", ".json"}:
                if not fields:
                    output_func(f"{path.name}: 선택 가능한 문자열 컬럼/Key가 없습니다.")
                    continue
                cache_key = (path.suffix.lower(), tuple(fields))
                selected = field_cache.get(cache_key)
                if selected is None:
                    selected = _choose_fields(path, fields, input_func, output_func)
                    if selected is None:
                        return None
                    field_cache[cache_key] = selected
                else:
                    output_func(f"{path.name}: 같은 구조의 이전 선택을 재사용합니다.")
                handler.select_fields(selected)
            units = handler.extract_units()
            handlers[path] = handler
            if remaining > 0:
                sample = "\n".join(unit.text for unit in units)[: min(3_000, remaining)]
                if sample:
                    samples.append(sample)
                    remaining -= len(sample)
        except (OSError, ValueError, ChatbotError) as error:
            output_func(f"{path.name}: 설정 실패 ({error})")
    return handlers, "\n".join(samples)


def _choose_fields(
    path: Path,
    fields: list[str],
    input_func: InputFunction,
    output_func: OutputFunction,
) -> list[str] | None:
    label = "컬럼" if path.suffix.lower() == ".csv" else "Key"
    output_func(f"{path.name} — 번역할 {label}을 선택하세요.")
    for index, field in enumerate(fields, 1):
        output_func(f"{index}. {field}")
    output_func("쉼표로 구분하여 여러 개 선택할 수 있습니다. 예: 2,3")
    while True:
        try:
            raw = input_func("선택: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        values = [value.strip() for value in raw.split(",")]
        if values and all(value.isdecimal() and 1 <= int(value) <= len(fields) for value in values):
            return list(dict.fromkeys(fields[int(value) - 1] for value in values))
        output_func("올바른 번호를 입력해 주세요.")


def main() -> int:
    return run_menu()


if __name__ == "__main__":
    raise SystemExit(main())
