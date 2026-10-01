"""ChatSessionStore와 ConversationHistory 복원 로직을 확인합니다. Qt 불필요."""

import json
import tempfile
import unittest
from pathlib import Path

from script.chat.history import ConversationHistory
from script.chat.session_store import ChatSessionStore, make_title


class MakeTitleTests(unittest.TestCase):
    def test_uses_first_message(self) -> None:
        self.assertEqual(make_title("TCP가 뭐야?"), "TCP가 뭐야?")

    def test_truncates_long_title(self) -> None:
        title = make_title("가" * 100)
        self.assertTrue(title.endswith("…"))
        self.assertLessEqual(len(title), 31)

    def test_empty_message_falls_back(self) -> None:
        self.assertEqual(make_title("   "), "새 대화")


class SessionStoreTests(unittest.TestCase):
    def _store(self, directory: str) -> ChatSessionStore:
        return ChatSessionStore(Path(directory) / "chat_history.json")

    def test_save_and_list_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            session_id = store.save_session(
                [
                    {"role": "user", "content": "TCP가 뭐야?"},
                    {"role": "assistant", "content": "전송 제어 프로토콜이야."},
                ]
            )
            self.assertIsNotNone(session_id)
            sessions = store.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["title"], "TCP가 뭐야?")
            self.assertEqual(sessions[0]["message_count"], 2)
            full = store.get_session(session_id or "")
            self.assertIsNotNone(full)
            assert full is not None
            self.assertEqual(len(full["messages"]), 2)

    def test_empty_conversation_is_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertIsNone(store.save_session([]))
            self.assertIsNone(store.save_session([{"role": "user", "content": "   "}]))
            self.assertEqual(store.list_sessions(), [])

    def test_update_existing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            session_id = store.save_session([{"role": "user", "content": "hi"}])
            assert session_id is not None
            store.save_session(
                [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                session_id=session_id,
            )
            self.assertEqual(len(store.get_messages(session_id)), 2)
            self.assertEqual(len(store.list_sessions()), 1)

    def test_same_title_sessions_have_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            first = store.save_session([{"role": "user", "content": "같은 질문"}])
            second = store.save_session([{"role": "user", "content": "같은 질문"}])
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(len(store.list_sessions()), 2)

    def test_delete_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            session_id = store.save_session([{"role": "user", "content": "hi"}])
            assert session_id is not None
            self.assertTrue(store.delete_session(session_id))
            self.assertEqual(store.list_sessions(), [])
            self.assertFalse(store.delete_session("no-such-id"))

    def test_reload_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat_history.json"
            first = ChatSessionStore(path)
            session_id = first.save_session([{"role": "user", "content": "hi"}])
            second = ChatSessionStore(path)
            self.assertEqual(len(second.list_sessions()), 1)
            self.assertEqual(second.get_session(session_id or "")["id"], session_id)

    def test_malformed_json_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat_history.json"
            path.write_text("{ broken json", encoding="utf-8")
            store = ChatSessionStore(path)
            self.assertEqual(store.list_sessions(), [])
            self.assertIsNone(store.get_session("anything"))

    def test_missing_file_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatSessionStore(Path(tmp) / "none" / "chat_history.json")
            self.assertEqual(store.list_sessions(), [])
            session_id = store.save_session([{"role": "user", "content": "hi"}])
            self.assertIsNotNone(session_id)

    def test_no_tmp_file_left_after_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.save_session([{"role": "user", "content": "hi"}])
            leftovers = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(leftovers, [])

    def test_only_user_assistant_roles_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            session_id = store.save_session(
                [
                    {"role": "user", "content": "hi"},
                    {"role": "system", "content": "secret"},
                    {"role": "assistant", "content": "hello"},
                ]
            )
            assert session_id is not None
            messages = store.get_messages(session_id)
            self.assertEqual([m["role"] for m in messages], ["user", "assistant"])


class ReplaceMessagesTests(unittest.TestCase):
    def test_replace_roundtrip(self) -> None:
        history = ConversationHistory()
        history.add_user_message("old")
        history.replace_messages(
            [
                {"role": "user", "content": "new q"},
                {"role": "assistant", "content": "new a"},
            ]
        )
        self.assertEqual(
            history.get_messages(),
            [
                {"role": "user", "content": "new q"},
                {"role": "assistant", "content": "new a"},
            ],
        )

    def test_replace_rejects_bad_shape(self) -> None:
        history = ConversationHistory()
        with self.assertRaises(ValueError):
            history.replace_messages([{"role": "admin", "content": "x"}])
        with self.assertRaises(ValueError):
            history.replace_messages([{"role": "user", "content": 123}])
        # 실패해도 기존 기록은 그대로 유지됩니다.
        self.assertEqual(history.get_messages(), [])

    def test_malformed_store_file_shape_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat_history.json"
            path.write_text(
                json.dumps({"conversations": "not-a-list"}), encoding="utf-8"
            )
            self.assertEqual(ChatSessionStore(path).list_sessions(), [])


if __name__ == "__main__":
    unittest.main()
