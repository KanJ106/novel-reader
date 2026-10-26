import types
import unittest
import tkinter as tk
from unittest.mock import patch

from app import NovelOverlayApp, TEXT_PAD_X, TEXT_PAD_Y, WINDOW_BG, convert_screen_point


class TestReader(NovelOverlayApp):
    def load_state(self):
        return {"books": [], "window": {}, "font_size": 10}

    def install_global_listeners(self):
        pass

    def restore_last_book_or_prompt(self):
        pass

    def schedule_state_save(self):
        pass

    def persist_runtime_state(self):
        pass


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.app = TestReader(self.root)
        self.root.update()
        self.app.reading_mode = True

    def tearDown(self):
        self.app.on_close()

    def load(self, text):
        self.app.loaded_text = text
        self.app.repaginate_content()
        self.app.show_page(0)
        self.root.update_idletasks()

    def test_chinese_lines_fit(self):
        self.load("\u4e2d\u6587\u6d4b\u8bd5\uff0c" * 300)
        width = self.app.text_widget.winfo_width() - 2 * TEXT_PAD_X
        for page in self.app.pages:
            for line in page.splitlines():
                self.assertLessEqual(self.app.text_font.measure(line), width)

    def test_one_wheel_event_turns_one_page(self):
        self.load("abcdef" * 1000)
        self.app.handle_mousewheel(types.SimpleNamespace(delta=-120))
        self.app.on_global_scroll(0, 0, 0, -1)
        self.app.process_input_events()
        self.assertEqual(self.app.current_page, 1)

    def test_progress_inside_long_paragraph(self):
        from pathlib import Path
        self.load("abcdef" * 1000)
        self.app.file_path = Path("test-book.txt")
        self.app.show_page(4)
        expected = self.app.pages[4]
        self.app.update_current_book_progress()
        entry = dict(self.app.books()[0])
        self.app.show_page(0)
        self.app.restore_progress(entry)
        self.assertEqual(self.app.current_page, 4)
        self.assertEqual(self.app.pages[self.app.current_page], expected)

    def test_key_callbacks_do_not_double_turn_even_when_delayed(self):
        from pynput import keyboard
        self.load("abcdef" * 1000)
        for _ in range(5):
            self.app.handle_direction_key("right", "local")
            self.app.on_global_key_press(keyboard.Key.right)
            self.app.process_input_events()
        self.assertEqual(self.app.current_page, 5)

    def test_all_text_preserved_and_visible_at_each_font_size(self):
        text = ("\u4e2d\u6587ABC\t123\uff0c\u3002\uff01" * 20 + "\n\n") * 3
        for size in (8, 10, 14, 18):
            self.app.text_font.configure(size=size)
            self.load(text)
            self.assertEqual("".join(self.app.pages).replace("\n", ""), text.replace("\n", ""))
            for index, page in enumerate(self.app.pages):
                self.app.show_page(index)
                self.root.update_idletasks()
                for number, line in enumerate(page.split("\n"), 1):
                    box = self.app.text_widget.dlineinfo(f"{number}.0")
                    self.assertIsNotNone(box)
                    x, y, width, height, _ = box
                    self.assertLessEqual(x + width, self.app.text_widget.winfo_width() - TEXT_PAD_X)
                    self.assertLessEqual(y + height, self.app.text_widget.winfo_height() - TEXT_PAD_Y)

    def test_font_change_keeps_original_position_on_screen(self):
        self.load("abcdef\u4e2d\u6587" * 1000)
        self.app.show_page(12)
        offset = self.app.current_page_offset()
        self.app.adjust_font(3)
        start = self.app.current_page_offset()
        end = self.app.page_source_offsets[self.app.current_page + 1]
        self.assertLessEqual(start, offset)
        self.assertLess(offset, end)

    def test_transparent_background_can_be_disabled(self):
        self.assertEqual(str(self.root.attributes("-transparentcolor")), WINDOW_BG)
        self.assertEqual(self.root.attributes("-alpha"), 1.0)
        self.app.transparent_background.set(False)
        self.app.apply_background()
        self.assertEqual(str(self.root.attributes("-transparentcolor")), "")

    def test_windows_background_is_fully_transparent(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        self.root.lift()
        self.root.update()
        point = wintypes.POINT(*convert_screen_point(
            self.root, self.root.winfo_rootx() + 20, self.root.winfo_rooty() + 20, to_physical=True))
        hit = user32.WindowFromPoint(point)
        self.assertNotEqual(user32.GetAncestor(hit, 2), user32.GetAncestor(self.root.winfo_id(), 2))

    def test_transparent_area_right_click_opens_chapters(self):
        x = self.app.text_widget.winfo_rootx() + 2
        y = self.app.text_widget.winfo_rooty() + 2
        self.app.chapter_marks = [("Chapter 1", 1)]
        with patch.object(self.app.menu, "tk_popup", side_effect=lambda *_: self.app.menu.invoke(3)) as popup:
            self.app.text_widget.event_generate("<Button-3>", x=2, y=2, rootx=x, rooty=y)
            popup.assert_called_once_with(x, y)
        self.assertTrue(self.app.has_open_child_window())
        self.assertEqual(self.app.child_windows[0].title(), "\u7ae0\u8282\u76ee\u5f55")
        self.assertTrue(self.app.visible)

    def test_child_window_click_does_not_steal_focus(self):
        self.app.chapter_marks = [("Chapter 1", 1)]
        self.app.open_chapter_selector()
        self.root.update()
        child = self.app.child_windows[0]
        with patch.object(self.root, "focus_force") as focus:
            self.app.handle_global_click_on_ui_thread(child.winfo_rootx() + 10, child.winfo_rooty() + 10)
            focus.assert_not_called()

    def test_menu_click_does_not_hide_reader_after_menu_closes(self):
        from pynput import mouse
        with patch.object(self.app.menu, "tk_popup", side_effect=lambda *_: self.app.on_global_click(-100, -100, mouse.Button.left, True)):
            self.app.popup_context_menu(10, 10)
        self.app.clear_context_menu_state()
        self.app.process_input_events()
        self.assertTrue(self.app.visible)

    def test_physical_click_outside_scaled_reader_hides_it(self):
        self.app.update_reader_bounds()
        left, top, right, bottom = self.app.reader_bounds
        self.app.handle_global_click_on_ui_thread(right + 100, bottom + 100)
        self.assertFalse(self.app.visible)

    def test_physical_click_inside_scaled_reader_keeps_it_visible(self):
        self.app.update_reader_bounds()
        left, top, right, bottom = self.app.reader_bounds
        self.app.handle_global_click_on_ui_thread(right - 2, bottom - 2)
        self.assertTrue(self.app.visible)

    def test_right_click_capture_uses_physical_pixels_and_releases_outside(self):
        from unittest.mock import Mock

        class Suppressed(Exception):
            pass

        self.app.update_reader_bounds()
        left, top, right, bottom = self.app.reader_bounds
        self.app.mouse_listener = Mock()
        self.app.mouse_listener.suppress_event.side_effect = Suppressed
        inside = types.SimpleNamespace(pt=types.SimpleNamespace(x=right - 2, y=bottom - 2))
        outside = types.SimpleNamespace(pt=types.SimpleNamespace(x=right + 100, y=bottom + 100))
        self.app.filter_mouse_event(0x0204, outside)
        self.app.mouse_listener.suppress_event.assert_not_called()
        with self.assertRaises(Suppressed):
            self.app.filter_mouse_event(0x0204, inside)
        with self.assertRaises(Suppressed):
            self.app.filter_mouse_event(0x0205, outside)
        with patch.object(self.app, "popup_context_menu") as popup:
            self.app.process_input_events()
            popup.assert_called_once_with(*convert_screen_point(self.root, right - 2, bottom - 2, to_physical=False))
        self.assertIsNone(self.app.right_click_captured)


if __name__ == "__main__":
    unittest.main()
