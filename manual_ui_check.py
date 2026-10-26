"""Isolated desktop fixture: real hooks and menus, no reading-state writes."""

import tkinter as tk

from app import NovelOverlayApp
from test_app import TestReader


class LiveReader(TestReader):
    def install_global_listeners(self):
        NovelOverlayApp.install_global_listeners(self)

    def on_close(self):
        surface = self.root.master
        after_id = getattr(surface, "reader_status_after_id", None)
        if after_id is not None:
            surface.after_cancel(after_id)
            surface.reader_status_after_id = None
        super().on_close()
        surface.destroy()


def main():
    surface = tk.Tk()
    surface.title("Reader Test Surface")
    surface.geometry("700x480+160+140")
    surface.configure(bg="#208064")
    tk.Label(surface, text="Transparent reader test surface", bg="#208064", fg="white").pack(pady=12)
    status = tk.Label(surface, bg="#208064", fg="white")
    status.pack(side="bottom", pady=15)
    entry = tk.Entry(surface)
    entry.insert(0, "Arrow keys also reach this window")
    entry.pack(side="bottom", fill="x", padx=20)
    counts = {"right_clicks": 0, "right_keys": 0}

    def count(name):
        counts[name] += 1

    surface.bind("<Button-3>", lambda event: count("right_clicks"))
    surface.bind("<Right>", lambda event: count("right_keys"))
    overlay = tk.Toplevel(surface)
    reader = LiveReader(overlay)
    overlay.title("Novel Reader UI Check")
    overlay.geometry("300x190+300+240")
    overlay.update()
    reader.loaded_text = "\n".join(
        f"Chapter {i}\n" + (f"Chapter {i}: sample text for paging and transparency. " * 30)
        for i in range(1, 4)
    )
    reader.chapter_marks = reader.extract_chapters(reader.loaded_text)
    reader.repaginate_content()
    reader.show_page(0)
    reader.reading_mode = True

    def refresh():
        if reader.closed:
            surface.destroy()
            return
        status.configure(text=f"Page: {reader.current_page + 1}; background right clicks: {counts['right_clicks']}; right keys: {counts['right_keys']}")
        surface.reader_status_after_id = surface.after(100, refresh)

    surface.protocol("WM_DELETE_WINDOW", reader.on_close)
    refresh()
    surface.mainloop()


if __name__ == "__main__":
    main()
