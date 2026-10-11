import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('fixed_publisher', Path(__file__).resolve().parents[1] / 'scripts/publish_douyin_groupbuy_batch.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class Page:
    first = property(lambda self: self)

    def __init__(self, preview):
        self.preview = preview

    def locator(self, *args): return self
    def nth(self, *args): return self
    def get_by_text(self, *args, **kwargs): return self
    def get_by_role(self, *args, **kwargs): return self
    async def click(self, **kwargs): pass
    async def wait_for(self, **kwargs): pass
    async def wait_for_timeout(self, *args): pass
    async def count(self): return 1
    async def set_input_files(self, *args): pass

    async def evaluate(self, script, *args):
        return [] if 'el.files' in script else self.preview


class CoverResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleared_input_with_matching_visible_preview_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cover.png'
            path.write_bytes(b'test fixture')
            result = await publisher.set_cover(Page({'mean_absolute_error': 3.8, 'width': 1086, 'height': 1448}), str(path))
        self.assertTrue(result['verified'])
        self.assertTrue(result['input_reset'])
        self.assertEqual(result['selected_filename'], 'cover.png')

    async def test_cleared_input_without_matching_preview_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cover.png'
            path.write_bytes(b'test fixture')
            with self.assertRaisesRegex(RuntimeError, '封面预览'):
                await publisher.set_cover(Page(None), str(path))


if __name__ == '__main__':
    unittest.main()
