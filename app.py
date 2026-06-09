import json
import queue
import re
import textwrap
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font, messagebox

from pynput import keyboard, mouse


APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "reader_state.json"

DEFAULT_WIDTH = 300
DEFAULT_HEIGHT = 190
DEFAULT_MARGIN = 14
DEFAULT_FONT_SIZE = 10
SHOW_HOTKEY = "<ctrl>+<alt>+<space>"
TEXT_COLOR = "#C8C8C8"
DRAG_THRESHOLD = 3
LINE_START_PUNCTUATION = "，。！？；：、,.!?;:)]）】》」』”’…"
WINDOW_BG = "#1e1e1e"
BORDER_COLOR = "#2a2a2a"
BORDER_WIDTH = 1
TEXT_PAD_X = 10
TEXT_PAD_Y = 8
TEXT_SPACING_TOP = 1
TEXT_SPACING_BOTTOM = 3
PAGE_BOTTOM_GUARD_LINES = 1

CHAPTER_PATTERN = re.compile(
    r"^\s*(?:"
    r"第[0-9零一二三四五六七八九十百千万两〇]+[章节回卷部篇集]"
    r"|[0-9]+[\.、]\s*"
    r"|chapter\s+[0-9ivxlcdm]+"
    r"|chap\.\s*[0-9ivxlcdm]+"
    r")",
    re.IGNORECASE,
)


def is_chapter_title(raw_line: str) -> bool:
    title = raw_line.strip()
    return bool(title and len(title) <= 40 and CHAPTER_PATTERN.match(title))


def wrap_display_line(raw_line: str, width: int) -> list[str]:
    wrapped_lines = textwrap.wrap(
        raw_line,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=False,
    )
    if not wrapped_lines:
        return [""]

    normalized_lines: list[str] = []
    for line in wrapped_lines:
        while normalized_lines and line and line[0] in LINE_START_PUNCTUATION:
            normalized_lines[-1] += line[0]
            line = line[1:]
        if line:
            normalized_lines.append(line)

    return normalized_lines or [""]


class NovelOverlayApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Novel Overlay")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg=BORDER_COLOR)

        self.state = self.load_state()
        self.file_path: Path | None = None
        self.loaded_text = ""
        self.chapter_marks: list[tuple[str, int]] = []
        self.pages: list[str] = []
        self.page_raw_line_ranges: list[tuple[int, int]] = []
        self.current_page = 0
        self.visible = True
        self.closed = False
        self.reading_mode = False
        self.context_menu_active = False
        self.last_show_time = 0.0
        self.drag_origin_x = 0
        self.drag_origin_y = 0
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.dragging = False
        self.move_mode = False
        self.child_windows: list[tk.Toplevel] = []
        self.hotkey_listener = None
        self.mouse_listener = None
        self.save_after_id: str | None = None
        self.last_layout_signature: tuple[int, int, int] | None = None
        self.input_events: queue.SimpleQueue[tuple] = queue.SimpleQueue()

        self.root.geometry(self.initial_geometry())

        self.text_font = font.Font(
            family="Microsoft YaHei UI",
            size=int(self.state.get("font_size", DEFAULT_FONT_SIZE)),
        )
        self.window_frame = tk.Frame(self.root, bg=BORDER_COLOR)
        self.window_frame.pack(fill="both", expand=True)
        self.text_widget = tk.Text(
            self.window_frame,
            wrap="none",
            bg=WINDOW_BG,
            fg=TEXT_COLOR,
            insertbackground=WINDOW_BG,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=TEXT_PAD_X,
            pady=TEXT_PAD_Y,
            font=self.text_font,
            cursor="arrow",
            spacing1=TEXT_SPACING_TOP,
            spacing3=TEXT_SPACING_BOTTOM,
        )
        self.text_widget.pack(fill="both", expand=True, padx=BORDER_WIDTH, pady=BORDER_WIDTH)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="移动窗口", command=lambda: self.run_menu_command(self.enable_move_mode))
        self.menu.add_command(label="打开小说  Ctrl+O", command=lambda: self.run_menu_command(self.open_book))
        self.menu.add_command(label="书架  Ctrl+B", command=lambda: self.run_menu_command(self.open_bookshelf))
        self.menu.add_command(label="章节目录  Ctrl+T", command=lambda: self.run_menu_command(self.open_chapter_selector))
        self.menu.add_separator()
        self.menu.add_command(label="字体缩小  Ctrl+-", command=lambda: self.run_menu_command(lambda: self.adjust_font(-1)))
        self.menu.add_command(label="字体放大  Ctrl+=", command=lambda: self.run_menu_command(lambda: self.adjust_font(1)))
        self.menu.add_separator()
        self.menu.add_command(label="退出  Esc", command=lambda: self.run_menu_command(self.on_close))

        self.bind_events()
        self.install_global_listeners()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(5, self.process_input_events)

        self.restore_last_book_or_prompt()

    def bind_events(self) -> None:
        for widget in (self.root, self.window_frame, self.text_widget):
            widget.bind("<space>", self.hide_window)
            widget.bind("<MouseWheel>", self.handle_mousewheel)
            widget.bind("<Button-4>", self.handle_mousewheel)
            widget.bind("<Button-5>", self.handle_mousewheel)
            widget.bind("<ButtonPress-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.perform_drag)
            widget.bind("<ButtonRelease-1>", self.finish_drag_or_enter_reading_mode)
            widget.bind("<Button-3>", self.show_context_menu)
            widget.bind("<Escape>", lambda _event: self.on_close())
            widget.bind("<Control-o>", lambda _event: self.open_book())
            widget.bind("<Control-b>", lambda _event: self.open_bookshelf())
            widget.bind("<Control-t>", lambda _event: self.open_chapter_selector())
            widget.bind("<Control-minus>", lambda _event: self.adjust_font(-1))
            widget.bind("<Control-equal>", lambda _event: self.adjust_font(1))
            widget.bind("<Left>", lambda _event: self.previous_page())
            widget.bind("<Right>", lambda _event: self.next_page())
            widget.bind("<Configure>", self.handle_configure)

    def load_state(self) -> dict:
        default_state = {"books": [], "last_book": None, "window": {}, "font_size": DEFAULT_FONT_SIZE}
        if not STATE_FILE.exists():
            return default_state
        try:
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default_state
        if not isinstance(loaded, dict):
            return default_state
        return {
            "books": loaded.get("books", []),
            "last_book": loaded.get("last_book"),
            "window": loaded.get("window", {}),
            "font_size": loaded.get("font_size", DEFAULT_FONT_SIZE),
        }

    def save_state(self) -> None:
        try:
            STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def schedule_state_save(self) -> None:
        if self.closed:
            return
        if self.save_after_id is not None:
            self.root.after_cancel(self.save_after_id)
        self.save_after_id = self.root.after(300, self.persist_runtime_state)

    def persist_runtime_state(self) -> None:
        self.save_after_id = None
        self.update_window_state()
        self.update_current_book_progress()
        self.save_state()

    def update_window_state(self) -> None:
        self.state["window"] = {
            "x": self.root.winfo_x(),
            "y": self.root.winfo_y(),
            "width": max(self.root.winfo_width(), DEFAULT_WIDTH),
            "height": max(self.root.winfo_height(), DEFAULT_HEIGHT),
        }
        self.state["font_size"] = int(self.text_font.cget("size"))

    def initial_geometry(self) -> str:
        window = self.state.get("window", {})
        width = max(int(window.get("width", DEFAULT_WIDTH)), DEFAULT_WIDTH)
        height = max(int(window.get("height", DEFAULT_HEIGHT)), DEFAULT_HEIGHT)
        x_pos = window.get("x")
        y_pos = window.get("y")
        if x_pos is None or y_pos is None:
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            x_pos = screen_width - width - DEFAULT_MARGIN
            y_pos = screen_height - height - DEFAULT_MARGIN - 40
        return f"{width}x{height}+{int(x_pos)}+{int(y_pos)}"

    def books(self) -> list[dict]:
        books = self.state.setdefault("books", [])
        if isinstance(books, list):
            return books
        self.state["books"] = []
        return self.state["books"]

    def find_book_entry(self, path: Path) -> dict | None:
        path_text = str(path)
        for book in self.books():
            if book.get("path") == path_text:
                return book
        return None

    def ensure_book_entry(self, path: Path) -> dict:
        entry = self.find_book_entry(path)
        if entry is None:
            entry = {"path": str(path), "title": path.stem, "page": 0}
            self.books().insert(0, entry)
        else:
            self.books().remove(entry)
            self.books().insert(0, entry)
        entry["title"] = path.stem
        return entry

    def restore_last_book_or_prompt(self) -> None:
        last_book = self.state.get("last_book")
        if isinstance(last_book, str):
            candidate = Path(last_book)
            if candidate.exists() and self.load_book(candidate, use_saved_progress=True):
                return
        self.open_book()

    def read_text_file(self, file_path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "utf-16", "big5"):
            try:
                return file_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
            except OSError:
                break
        return ""

    def open_book(self) -> None:
        file_name = filedialog.askopenfilename(
            title="选择小说 TXT 文件",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not file_name:
            if self.file_path is None:
                self.on_close()
            return
        self.load_book(Path(file_name), use_saved_progress=True)

    def load_book(self, file_path: Path, use_saved_progress: bool) -> bool:
        content = self.read_text_file(file_path)
        if not content.strip():
            messagebox.showerror("打开失败", "文件内容为空，或无法识别文本编码。")
            return False

        self.file_path = file_path
        self.loaded_text = content
        self.chapter_marks = self.extract_chapters(content)
        self.root.title(file_path.stem)

        self.repaginate_content()
        entry = self.ensure_book_entry(file_path)
        self.state["last_book"] = str(file_path)

        if use_saved_progress:
            self.restore_progress(entry)
        else:
            self.show_page(0)

        self.reading_mode = True
        self.last_show_time = time.monotonic()
        self.persist_runtime_state()
        self.root.focus_force()
        return True

    def repaginate_content(self) -> None:
        self.root.update_idletasks()
        width = max(self.text_widget.winfo_width(), DEFAULT_WIDTH)
        height = max(self.text_widget.winfo_height(), DEFAULT_HEIGHT)
        font_size = int(self.text_font.cget("size"))
        self.last_layout_signature = (width, height, font_size)

        char_width = max(
            self.text_font.measure("WW") // 2,
            self.text_font.measure("MM") // 2,
            self.text_font.measure("00") // 2,
            1,
        )
        line_height = max(self.text_font.metrics("linespace") + TEXT_SPACING_TOP + TEXT_SPACING_BOTTOM, 1)
        content_width = max(width - (TEXT_PAD_X * 2), 120)
        content_height = max(height - (TEXT_PAD_Y * 2), 60)
        chars_per_line = max((content_width // char_width) - 1, 6)
        lines_per_page = max((content_height // line_height) - PAGE_BOTTOM_GUARD_LINES, 3)

        pages: list[str] = []
        raw_line_ranges: list[tuple[int, int]] = []
        current_lines: list[str] = []
        page_start_raw_line = 1
        current_page_raw_lines: set[int] = set()

        def flush_page(next_start_raw_line: int) -> None:
            nonlocal current_lines, page_start_raw_line, current_page_raw_lines
            if not current_lines:
                page_start_raw_line = next_start_raw_line
                return
            end_raw_line = max(current_page_raw_lines) if current_page_raw_lines else page_start_raw_line
            pages.append("\n".join(current_lines))
            raw_line_ranges.append((page_start_raw_line, end_raw_line))
            current_lines = []
            current_page_raw_lines = set()
            page_start_raw_line = next_start_raw_line

        for raw_line_no, raw_line in enumerate(self.loaded_text.splitlines(), start=1):
            if is_chapter_title(raw_line) and current_lines:
                flush_page(raw_line_no)

            wrapped_lines = wrap_display_line(raw_line, chars_per_line)

            for wrapped_index, line in enumerate(wrapped_lines):
                current_lines.append(line)
                current_page_raw_lines.add(raw_line_no)
                if len(current_lines) >= lines_per_page:
                    has_more_wrapped_lines = wrapped_index < len(wrapped_lines) - 1
                    next_start_raw_line = raw_line_no if has_more_wrapped_lines else raw_line_no + 1
                    flush_page(next_start_raw_line)

        if current_lines:
            flush_page(page_start_raw_line)

        if not pages:
            pages = [""]
            raw_line_ranges = [(1, 1)]

        self.pages = pages
        self.page_raw_line_ranges = raw_line_ranges
        self.current_page = min(self.current_page, len(self.pages) - 1)

    def show_page(self, page_index: int) -> None:
        if not self.pages:
            self.pages = [self.loaded_text]
            self.page_raw_line_ranges = [(1, 1)]
        self.current_page = min(max(page_index, 0), len(self.pages) - 1)
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", self.pages[self.current_page])
        self.text_widget.configure(state="disabled")

    def restore_progress(self, entry: dict) -> None:
        page_start = entry.get("page_start")
        if isinstance(page_start, int):
            self.show_page(self.find_page_for_raw_line(page_start))
            return
        page = int(entry.get("page", 0))
        self.show_page(page)

    def extract_chapters(self, content: str) -> list[tuple[str, int]]:
        chapters: list[tuple[str, int]] = []
        for line_no, raw_line in enumerate(content.splitlines(), start=1):
            if is_chapter_title(raw_line):
                chapters.append((raw_line.strip(), line_no))
        return chapters

    def update_current_book_progress(self) -> None:
        if self.file_path is None:
            return
        entry = self.ensure_book_entry(self.file_path)
        entry["page"] = self.current_page
        entry["page_start"] = self.current_page_start_line()
        entry.pop("page_preview", None)
        entry.pop("progress", None)
        self.state["last_book"] = str(self.file_path)

    def next_page(self) -> str:
        self.show_page(self.current_page + 1)
        self.schedule_state_save()
        return "break"

    def previous_page(self) -> str:
        self.show_page(self.current_page - 1)
        self.schedule_state_save()
        return "break"

    def handle_mousewheel(self, event: tk.Event) -> str:
        if getattr(event, "delta", 0) > 0 or getattr(event, "num", None) == 4:
            return self.previous_page()
        return self.next_page()

    def start_drag(self, event: tk.Event) -> str:
        self.drag_origin_x = event.x_root
        self.drag_origin_y = event.y_root
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        self.dragging = False
        event.widget.grab_set()
        return "break"

    def perform_drag(self, event: tk.Event) -> str:
        delta_x = event.x_root - self.drag_origin_x
        delta_y = event.y_root - self.drag_origin_y
        if not self.dragging:
            total_delta_x = abs(event.x_root - self.drag_start_x)
            total_delta_y = abs(event.y_root - self.drag_start_y)
            if total_delta_x < DRAG_THRESHOLD and total_delta_y < DRAG_THRESHOLD:
                return "break"
            self.dragging = True
        self.root.geometry(f"+{self.root.winfo_x() + delta_x}+{self.root.winfo_y() + delta_y}")
        self.drag_origin_x = event.x_root
        self.drag_origin_y = event.y_root
        self.schedule_state_save()
        return "break"

    def finish_drag_or_enter_reading_mode(self, event: tk.Event | None = None) -> str:
        if event is not None:
            try:
                event.widget.grab_release()
            except tk.TclError:
                pass
        if self.dragging:
            self.dragging = False
            self.disable_move_mode()
            return "break"
        if self.move_mode:
            self.disable_move_mode()
        return self.enter_reading_mode()

    def enter_reading_mode(self, _event: tk.Event | None = None) -> str:
        if self.visible:
            self.reading_mode = True
            self.last_show_time = 0.0
            self.root.focus_force()
        return "break"

    def enable_move_mode(self) -> None:
        self.move_mode = True
        self.reading_mode = False
        self.root.configure(cursor="fleur")
        self.text_widget.configure(cursor="fleur")
        self.root.focus_force()

    def disable_move_mode(self) -> None:
        if not self.move_mode:
            return
        self.move_mode = False
        self.root.configure(cursor="")
        self.text_widget.configure(cursor="arrow")

    def handle_configure(self, _event: tk.Event) -> None:
        current_signature = (
            max(self.text_widget.winfo_width(), DEFAULT_WIDTH),
            max(self.text_widget.winfo_height(), DEFAULT_HEIGHT),
            int(self.text_font.cget("size")),
        )
        if current_signature != self.last_layout_signature and self.loaded_text:
            previous_line = self.current_page_start_line()
            self.repaginate_content()
            self.show_page(self.find_page_for_raw_line(previous_line))
        self.schedule_state_save()

    def adjust_font(self, delta: int) -> None:
        current_size = int(self.text_font.cget("size"))
        new_size = min(max(current_size + delta, 8), 18)
        if new_size == current_size:
            return
        self.text_font.configure(size=new_size)
        if self.loaded_text:
            previous_line = self.current_page_start_line()
            self.repaginate_content()
            self.show_page(self.find_page_for_raw_line(previous_line))
        self.schedule_state_save()

    def install_global_listeners(self) -> None:
        self.hotkey_listener = keyboard.GlobalHotKeys({SHOW_HOTKEY: lambda: self.input_events.put(("show",))})
        self.hotkey_listener.start()
        self.mouse_listener = mouse.Listener(on_click=self.on_global_click, on_scroll=self.on_global_scroll)
        self.mouse_listener.start()

    def on_global_click(self, x_pos: int, y_pos: int, _button: mouse.Button, pressed: bool) -> None:
        if not pressed or self.closed:
            return
        self.input_events.put(("click", x_pos, y_pos))

    def handle_global_click_on_ui_thread(self, x_pos: int, y_pos: int) -> None:
        if not self.visible or self.context_menu_active:
            return
        if self.is_point_inside_app(x_pos, y_pos):
            self.enter_reading_mode()
        elif self.reading_mode:
            self.hide_window()

    def on_global_scroll(self, _x_pos: int, _y_pos: int, _dx: int, dy: int) -> None:
        if self.closed or dy == 0:
            return
        self.input_events.put(("scroll", dy))

    def process_input_events(self) -> None:
        if self.closed:
            return
        while True:
            try:
                event = self.input_events.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "show":
                self.show_window()
            elif kind == "click":
                _, x_pos, y_pos = event
                self.handle_global_click_on_ui_thread(x_pos, y_pos)
            elif kind == "scroll":
                _, dy = event
                if self.visible and self.reading_mode:
                    if dy > 0:
                        self.previous_page()
                    else:
                        self.next_page()
        self.root.after(5, self.process_input_events)

    def is_point_inside_app(self, x_pos: int, y_pos: int) -> bool:
        for window in [self.root, *self.child_windows]:
            if not window.winfo_exists():
                continue
            left = window.winfo_rootx()
            top = window.winfo_rooty()
            right = left + window.winfo_width()
            bottom = top + window.winfo_height()
            if left <= x_pos <= right and top <= y_pos <= bottom:
                return True
        return False

    def show_context_menu(self, event: tk.Event) -> str:
        self.context_menu_active = True
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
            self.root.after(120, self.clear_context_menu_state)
        return "break"

    def clear_context_menu_state(self) -> None:
        self.context_menu_active = False

    def run_menu_command(self, command) -> None:
        self.context_menu_active = False
        command()

    def open_bookshelf(self) -> None:
        dialog = tk.Toplevel(self.root)
        self.register_child_window(dialog)
        dialog.title("书架")
        dialog.attributes("-topmost", True)
        dialog.geometry("420x320")
        dialog.configure(bg="#151515")

        listbox = tk.Listbox(
            dialog,
            bg="#101010",
            fg="#E8E8E8",
            selectbackground="#404040",
            selectforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
        )
        listbox.pack(fill="both", expand=True, padx=10, pady=10)

        books = self.books()
        for book in books:
            title = book.get("title") or Path(book.get("path", "")).stem
            page = int(book.get("page", 0)) + 1
            listbox.insert("end", f"{title}  (第 {page} 页)")

        button_bar = tk.Frame(dialog, bg="#151515")
        button_bar.pack(fill="x", padx=10, pady=(0, 10))

        def current_entry() -> dict | None:
            selection = listbox.curselection()
            return books[selection[0]] if selection else None

        def open_selected() -> None:
            entry = current_entry()
            if entry is None:
                return
            path = Path(entry["path"])
            if not path.exists():
                messagebox.showerror("文件不存在", f"找不到文件：\n{path}")
                return
            if self.load_book(path, use_saved_progress=True):
                self.close_child_window(dialog)

        def remove_selected() -> None:
            entry = current_entry()
            if entry is None:
                return
            books.remove(entry)
            self.save_state()
            self.close_child_window(dialog)

        tk.Button(button_bar, text="打开", command=open_selected).pack(side="left")
        tk.Button(button_bar, text="移除", command=remove_selected).pack(side="left", padx=8)
        tk.Button(button_bar, text="导入新小说", command=lambda: (self.close_child_window(dialog), self.open_book())).pack(side="left")

        listbox.bind("<Double-Button-1>", lambda _event: open_selected())
        dialog.focus_force()

    def open_chapter_selector(self) -> None:
        if not self.chapter_marks:
            messagebox.showinfo("没有目录", "没有识别到章节标题。")
            return

        dialog = tk.Toplevel(self.root)
        self.register_child_window(dialog)
        dialog.title("章节目录")
        dialog.attributes("-topmost", True)
        dialog.geometry("420x360")
        dialog.configure(bg="#151515")

        filter_var = tk.StringVar()
        search_entry = tk.Entry(dialog, textvariable=filter_var)
        search_entry.pack(fill="x", padx=10, pady=(10, 6))

        listbox = tk.Listbox(
            dialog,
            bg="#101010",
            fg="#E8E8E8",
            selectbackground="#404040",
            selectforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
        )
        listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        visible_chapters: list[tuple[str, int]] = []

        def refresh_list(*_args: object) -> None:
            keyword = filter_var.get().strip().lower()
            listbox.delete(0, "end")
            visible_chapters.clear()
            for title, line_no in self.chapter_marks:
                if keyword and keyword not in title.lower():
                    continue
                visible_chapters.append((title, line_no))
                listbox.insert("end", title)

        def jump_selected() -> None:
            selection = listbox.curselection()
            if not selection:
                return
            _title, line_no = visible_chapters[selection[0]]
            target_page = self.find_page_for_raw_line(line_no)
            self.show_page(target_page)
            self.schedule_state_save()
            self.close_child_window(dialog)

        filter_var.trace_add("write", refresh_list)
        refresh_list()
        tk.Button(dialog, text="跳转", command=jump_selected).pack(pady=(0, 10))
        listbox.bind("<Double-Button-1>", lambda _event: jump_selected())
        search_entry.focus_set()

    def find_page_for_raw_line(self, line_no: int) -> int:
        for page_no, (start_line, _end_line) in enumerate(self.page_raw_line_ranges):
            if start_line == line_no:
                return page_no
        for page_no, (start_line, end_line) in enumerate(self.page_raw_line_ranges):
            if start_line <= line_no <= end_line:
                return page_no
        return 0

    def current_page_start_line(self) -> int:
        if not self.page_raw_line_ranges:
            return 1
        page_index = min(max(self.current_page, 0), len(self.page_raw_line_ranges) - 1)
        return self.page_raw_line_ranges[page_index][0]

    def register_child_window(self, window: tk.Toplevel) -> None:
        self.child_windows.append(window)
        window.protocol("WM_DELETE_WINDOW", lambda win=window: self.close_child_window(win))

    def close_child_window(self, window: tk.Toplevel) -> None:
        if window in self.child_windows:
            self.child_windows.remove(window)
        if window.winfo_exists():
            window.destroy()

    def hide_window(self, _event: tk.Event | None = None) -> str:
        if self.visible:
            self.visible = False
            self.reading_mode = False
            self.persist_runtime_state()
            self.root.withdraw()
        return "break"

    def show_window(self) -> None:
        if self.visible:
            return
        self.visible = True
        self.reading_mode = True
        self.root.deiconify()
        self.root.update_idletasks()
        self.root.lift()
        self.root.focus_force()
        self.last_show_time = time.monotonic()

    def on_close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.save_after_id is not None:
            self.root.after_cancel(self.save_after_id)
            self.save_after_id = None
        try:
            for after_id in self.root.tk.call("after", "info"):
                self.root.after_cancel(after_id)
        except tk.TclError:
            pass
        self.persist_runtime_state()
        if self.mouse_listener is not None:
            self.mouse_listener.stop()
            self.mouse_listener = None
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
            self.hotkey_listener = None
        for child in list(self.child_windows):
            self.close_child_window(child)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    NovelOverlayApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
