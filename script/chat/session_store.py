"""최근 대화를 JSON 파일에 영속 저장합니다. GUI 의존성이 없습니다."""

from datetime import datetime
import json
from pathlib import Path
import uuid

from script.config.settings import PROJECT_ROOT

CHAT_HISTORY_PATH = PROJECT_ROOT / "setting" / "chat_history.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def make_title(first_user_message: str, max_length: int = 30) -> str:
    """Gemini 추가 호출 없이 첫 사용자 메시지로 제목을 만듭니다."""
    title = " ".join(first_user_message.split())
    if len(title) > max_length:
        return title[:max_length] + "…"
    return title or "새 대화"


class ChatSessionStore:
    """대화 session의 목록/조회/저장/삭제를 담당합니다."""

    def __init__(self, path: Path = CHAT_HISTORY_PATH) -> None:
        self.path = path

    # ---------- 조회 ----------
    def list_sessions(self) -> list[dict]:
        """updated_at 내림차순 session 요약 목록을 반환합니다."""
        data = self._read_or_empty()
        sessions = [s for s in data["conversations"] if isinstance(s, dict)]
        sessions.sort(key=lambda s: str(s.get("updated_at", "")), reverse=True)
        return [
            {
                "id": session.get("id", ""),
                "title": session.get("title", "새 대화"),
                "updated_at": session.get("updated_at", ""),
                "message_count": len(session.get("messages", []))
                if isinstance(session.get("messages"), list)
                else 0,
            }
            for session in sessions
            if isinstance(session.get("id"), str) and session.get("id")
        ]

    def get_session(self, session_id: str) -> dict | None:
        for session in self._read_or_empty()["conversations"]:
            if isinstance(session, dict) and session.get("id") == session_id:
                messages = session.get("messages")
                if not isinstance(messages, list):
                    return None
                return {
                    "id": session.get("id"),
                    "title": session.get("title", "새 대화"),
                    "created_at": session.get("created_at", ""),
                    "updated_at": session.get("updated_at", ""),
                    "messages": [
                        {"role": m.get("role"), "content": m.get("content")}
                        for m in messages
                        if isinstance(m, dict)
                    ],
                }
        return None

    def get_messages(self, session_id: str) -> list[dict]:
        session = self.get_session(session_id)
        return list(session["messages"]) if session is not None else []

    # ---------- 저장/삭제 ----------
    def save_session(
        self,
        messages: list[dict],
        session_id: str | None = None,
        title: str | None = None,
    ) -> str | None:
        """성공한 user/assistant pair를 저장합니다. 빈 대화는 저장하지 않고 None 반환."""
        cleaned = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if isinstance(m, dict)
            and m.get("role") in {"user", "assistant"}
            and isinstance(m.get("content"), str)
            and m.get("content").strip()
        ]
        if not cleaned:
            return None
        data = self._read_or_empty()
        if session_id is not None:
            for session in data["conversations"]:
                if isinstance(session, dict) and session.get("id") == session_id:
                    session["messages"] = cleaned
                    session["updated_at"] = _now_iso()
                    if title:
                        session["title"] = title
                    self._atomic_write(data)
                    return session_id
        first_user = next(
            (m["content"] for m in cleaned if m["role"] == "user"), "새 대화"
        )
        new_id = uuid.uuid4().hex
        now = _now_iso()
        data["conversations"].append(
            {
                "id": new_id,
                "title": title or make_title(first_user),
                "created_at": now,
                "updated_at": now,
                "messages": cleaned,
            }
        )
        self._atomic_write(data)
        return new_id

    def delete_session(self, session_id: str) -> bool:
        data = self._read_or_empty()
        kept = [
            s
            for s in data["conversations"]
            if not (isinstance(s, dict) and s.get("id") == session_id)
        ]
        if len(kept) == len(data["conversations"]):
            return False
        data["conversations"] = kept
        self._atomic_write(data)
        return True

    # ---------- 파일 IO ----------
    def _read_or_empty(self) -> dict:
        try:
            if not self.path.is_file():
                return {"conversations": []}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("conversations"), list)
            ):
                return {"conversations": []}
            return data
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # 손상된 파일 때문에 앱 전체가 죽지 않도록 빈 목록으로 시작합니다.
            return {"conversations": []}

    def _atomic_write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
