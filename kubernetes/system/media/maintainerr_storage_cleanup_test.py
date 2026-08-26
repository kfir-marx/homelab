import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("maintainerr_storage_cleanup.py")
SPEC = importlib.util.spec_from_file_location("maintainerr_storage_cleanup", MODULE_PATH)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


class CandidateOrderingTest(unittest.TestCase):
    def candidate(self, title, watched, age, size):
        return cleanup.Candidate(title, "movie", title, watched, age, size, 1)

    def test_watched_items_sort_before_unwatched_items(self):
        candidates = [
            self.candidate("old-unwatched", False, 1, 100),
            self.candidate("new-watched", True, 2, 1),
        ]
        candidates.sort(key=cleanup.Candidate.sort_key)
        self.assertEqual([item.title for item in candidates], ["new-watched", "old-unwatched"])

    def test_older_downloads_sort_first_within_watch_state(self):
        candidates = [
            self.candidate("new", True, 2, 100),
            self.candidate("old", True, 1, 1),
        ]
        candidates.sort(key=cleanup.Candidate.sort_key)
        self.assertEqual([item.title for item in candidates], ["old", "new"])

    def test_larger_files_break_equal_age_ties(self):
        candidates = [
            self.candidate("episode", True, 1, 2),
            self.candidate("movie", True, 1, 5),
        ]
        candidates.sort(key=cleanup.Candidate.sort_key)
        self.assertEqual([item.title for item in candidates], ["movie", "episode"])


class ProtectionTest(unittest.TestCase):
    def test_parent_favorite_protects_episode(self):
        item = {"id": "episode", "parentId": "season", "grandparentId": "show"}
        self.assertTrue(cleanup.is_protected_item(item, {"show"}))

    def test_maintainerr_exclusion_protects_item(self):
        item = {"id": "movie", "maintainerrExclusionType": "global"}
        self.assertTrue(cleanup.is_protected_item(item, set()))


if __name__ == "__main__":
    unittest.main()
