import re
import unittest
from pathlib import Path

WORDS_FILE = Path(__file__).parents[1] / "frontend" / "words.ts"


class ArcadeWordListTests(unittest.TestCase):
    def test_words_are_unique_uppercase_and_four_to_eight_letters(self):
        words = re.findall(r'"([^"]*)"', WORDS_FILE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(words), 300)
        self.assertEqual(len(words), len(set(words)))
        for word in words:
            self.assertRegex(word, r"^[A-Z]{4,8}$")


if __name__ == "__main__":
    unittest.main()
