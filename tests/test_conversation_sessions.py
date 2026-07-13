import os
import sys
import unittest


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from conversation_sessions import build_conversation_sessions


class ConversationSessionTests(unittest.TestCase):
    def row(self, trace_id, buyer, minute, question="问题"):
        return {
            "traceId": trace_id,
            "buyerIdRaw": buyer,
            "buyerId": buyer,
            "timestamp": minute * 60 * 1000,
            "question": question,
            "answer": "回复",
        }

    def test_groups_same_buyer_within_sixty_minutes(self):
        sessions = build_conversation_sessions([
            self.row("trace-2", "buyer-1", 60),
            self.row("trace-1", "buyer-1", 1),
            self.row("trace-3", "buyer-1", 121),
        ], "shop-1")

        self.assertEqual(len(sessions), 2)
        multi = next(session for session in sessions if session["isMultiTurn"])
        self.assertEqual(multi["traceIds"], ["trace-1", "trace-2"])
        self.assertEqual(multi["turnCount"], 2)

    def test_keeps_buyer_and_missing_identity_records_separate(self):
        sessions = build_conversation_sessions([
            self.row("trace-1", "buyer-1", 0),
            self.row("trace-2", "buyer-2", 1),
            self.row("trace-3", "", 2),
            self.row("trace-4", "", 3),
        ], "shop-1")

        self.assertEqual(len(sessions), 4)
        self.assertFalse(any(session["isMultiTurn"] for session in sessions))

    def test_session_ids_are_stable_for_same_input(self):
        rows = [self.row("trace-1", "buyer-1", 0), self.row("trace-2", "buyer-1", 10)]
        first = build_conversation_sessions(rows, "shop-1")
        second = build_conversation_sessions(list(reversed(rows)), "shop-1")
        self.assertEqual(first[0]["id"], second[0]["id"])


if __name__ == "__main__":
    unittest.main()
