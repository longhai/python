import os, sys, re, shutil, threading, urllib.request, json, xml.etree.ElementTree as ET 
import tkinter as tk
from tkinter import Tk, Frame, Label, Button, Listbox, Scrollbar, Text, END, filedialog, StringVar, Entry, messagebox, ttk

API_URL = "https://api.github.com/repos/longhai/xml/contents/?ref=main"

# -----------------------
# Helpers
# -----------------------
def fetch_url(url, timeout=30, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "python-urllib/3"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            if i == retries - 1:
                raise e
            threading.Event().wait(1)

def fetch_json(url):
    return json.loads(fetch_url(url).decode("utf-8"))

def fetch_text(url):
    data = fetch_url(url)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")

def normalize_text(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u00C0-\u024f\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# -----------------------
# Parse XML: parent + clone tùy chọn
# -----------------------
def parse_xml_games(xml_text, skip_keywords=None, include_clones=False):
    """Lấy danh sách game từ XML (chỉ parent hoặc cả clone tùy chọn)."""
    try:
        root = ET.fromstring(xml_text)
        games = []
        for g in root.iter("game"):
            if not include_clones and g.get("cloneof"):
                continue
            games.append({"name": g.get("name")})
        if skip_keywords:
            keywords = [k.strip().lower() for k in skip_keywords.split(",") if k.strip()]
            games = [g for g in games if all(kw not in g["name"].lower() for kw in keywords)]
        return games
    except ET.ParseError:
        return []

# -----------------------
# Core copy processor
# -----------------------
class CopyOnlyProcessor:
    def __init__(self, source_dir, dest_dir, xml_file, games, extensions=None):
        self.source_dir = source_dir
        self.dest_dir = dest_dir
        self.xml_file = xml_file
        self.games = games
        self.extensions = extensions
        self.copied_count = 0
        self.lock = threading.Lock()

    def process(self, progress_callback=None, log_callback=None):
        dst = os.path.join(
            self.dest_dir,
            os.path.splitext(os.path.basename(self.xml_file))[0]
        ) if self.xml_file else self.dest_dir
        os.makedirs(dst, exist_ok=True)

        files = [
            os.path.join(self.source_dir, f)
            for f in os.listdir(self.source_dir)
            if os.path.isfile(os.path.join(self.source_dir, f))
        ]
        if not files:
            if log_callback:
                log_callback("Không có file trong thư mục nguồn.")
            return 0, 0

        file_map = {
            normalize_text(os.path.splitext(os.path.basename(f))[0]): f
            for f in files
            if not self.extensions or os.path.splitext(f)[1].lower() in self.extensions
        }

        total = len(self.games)
        results = []

        for i, game in enumerate(self.games):
            game_name_norm = normalize_text(game["name"])
            match = file_map.get(game_name_norm)
            if not match:
                results.append(f"[MISS] {game['name']}")
            else:
                dst_path = os.path.join(dst, os.path.basename(match))
                if os.path.exists(dst_path):
                    results.append(f"[SKIP] {os.path.basename(dst_path)}")
                else:
                    try:
                        shutil.copy2(match, dst_path)
                        with self.lock:
                            self.copied_count += 1
                        results.append(f"[OK] {os.path.basename(match)}")
                    except Exception as e:
                        results.append(f"[ERR] {os.path.basename(match)}: {e}")

            if progress_callback:
                progress_callback(i + 1, total)
            if log_callback:
                log_callback(results[-1])

        success_count = sum(1 for r in results if r.startswith("[OK]"))
        return success_count, total

# -----------------------
# UI
# -----------------------
class CopyParentApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Copy no-intro Parent/Clone Games")
        self.root.geometry("500x650")
        self.root.minsize(400, 600)

        self.source_dir = StringVar()
        self.dest_dir = StringVar(value=os.path.dirname(os.path.abspath(sys.argv[0])))
        self.extensions = StringVar(value=".zip,.7z,.rar")
        self.skip_keywords = StringVar(value="bios,in-1,demo")
        self.include_clones = tk.BooleanVar(value=False)

        self.xml_list = []
        self.filtered_xml = []
        self.games = []
        self.current_xml_file = None

        self._search_after_id = None
        self.SEARCH_DEBOUNCE_MS = 300
        
        self.progress = None
        self.progress_label = None

        self.build_ui()
        self.threaded_fetch_xml_list()

    def build_ui(self):
        main_frame = Frame(self.root, padx=10, pady=10)
        main_frame.pack(fill="both", expand=True)
        
        # Top
        top_frame = Frame(main_frame, bd=2, relief="groove", padx=8, pady=6)
        top_frame.pack(fill="both", expand=True, pady=(0, 10))
        top_frame.columnconfigure(1, weight=1)
        top_frame.rowconfigure(1, weight=1)

        Label(top_frame, text="Tìm XML:").grid(row=0, column=0, sticky="w", pady=4)
        self.search_var = StringVar()
        Entry(top_frame, textvariable=self.search_var).grid(row=0, column=1, sticky="we", padx=4)
        Button(top_frame, text="Reset", command=self.reset_xml_list, width=8).grid(row=0, column=2, padx=4)

        self.lb_xml = Listbox(top_frame)
        self.lb_xml.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=6)
        sb_xml = Scrollbar(top_frame, orient="vertical", command=self.lb_xml.yview)
        sb_xml.grid(row=1, column=3, sticky="ns")
        self.lb_xml.config(yscrollcommand=sb_xml.set)
        self.lb_xml.bind("<<ListboxSelect>>", self.on_xml_select)

        # Middle
        mid_frame = Frame(main_frame, bd=2, relief="groove", padx=8, pady=6)
        mid_frame.pack(fill="x", pady=(0, 10))
        mid_frame.columnconfigure(1, weight=1)

        Label(mid_frame, text="Thư mục nguồn:").grid(row=0, column=0, sticky="w", pady=4)
        Entry(mid_frame, textvariable=self.source_dir).grid(row=0, column=1, sticky="we", padx=4)
        Button(mid_frame, text="Chọn...", command=self.choose_source, width=8).grid(row=0, column=2, padx=4)

        Label(mid_frame, text="Thư mục đích:").grid(row=1, column=0, sticky="w", pady=4)
        Entry(mid_frame, textvariable=self.dest_dir).grid(row=1, column=1, sticky="we", padx=4)
        Button(mid_frame, text="Chọn...", command=self.choose_dest, width=8).grid(row=1, column=2, padx=4)
        
        Label(mid_frame, text="Phần mở rộng:").grid(row=2, column=0, sticky="w", pady=4)
        Entry(mid_frame, textvariable=self.extensions).grid(row=2, column=1, sticky="we", padx=4)

        Label(mid_frame, text="Từ khóa bỏ qua:").grid(row=3, column=0, sticky="w", pady=4)
        Entry(mid_frame, textvariable=self.skip_keywords).grid(row=3, column=1, sticky="we", padx=4)

        ttk.Checkbutton(mid_frame, text="Bao gồm cả Clone Games", variable=self.include_clones).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=4
        )

        progress_frame = Frame(mid_frame)
        progress_frame.grid(row=5, column=0, columnspan=3, sticky="we", pady=8)
        self.progress_label = Label(progress_frame, text="Sẵn sàng")
        self.progress_label.pack(side="top", fill="x")
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="top", fill="x")

        Button(mid_frame, text="Copy Games", command=self.threaded_copy, 
               bg="#4CAF50", fg="white", width=15).grid(row=6, column=1, pady=8)

        # Bottom
        bot_frame = Frame(main_frame, bd=2, relief="groove", padx=8, pady=6)
        bot_frame.pack(fill="both", expand=True)
        bot_frame.columnconfigure(0, weight=1)
        bot_frame.rowconfigure(0, weight=1)

        Label(bot_frame, text="Logs:").pack(anchor="w")
        self.txt_log = Text(bot_frame)
        self.txt_log.pack(fill="both", expand=True, side="left")
        sb_log = Scrollbar(bot_frame, orient="vertical", command=self.txt_log.yview)
        sb_log.pack(side="right", fill="y")
        self.txt_log.config(yscrollcommand=sb_log.set)

        self.search_var.trace_add("write", self._on_search_var_changed)

    # -----------------------
    # UI helpers
    # -----------------------
    def log(self, msg):
        self.txt_log.insert(END, msg + "\n")
        self.txt_log.see(END)

    def update_progress(self, current, total):
        self.progress["value"] = (current / total) * 100
        self.progress_label.config(text=f"Đang xử lý: {current}/{total}")
        if current == total:
            self.root.after(1000, lambda: self.progress_label.config(text="Hoàn thành"))

    def choose_source(self):
        if d := filedialog.askdirectory(title="Chọn thư mục nguồn"):
            self.source_dir.set(d)

    def choose_dest(self):
        if d := filedialog.askdirectory(title="Chọn thư mục đích"):
            self.dest_dir.set(d)

    # -----------------------
    # XML list
    # -----------------------
    def threaded_fetch_xml_list(self):
        threading.Thread(target=self.fetch_xml_list, daemon=True).start()

    def fetch_xml_list(self):
        self.log("Đang tải danh sách XML từ GitHub...")
        try:
            data = fetch_json(API_URL)
            self.xml_list = sorted([f for f in data if f["name"].endswith(".xml")], 
                                   key=lambda x: x["name"].lower())
            self.filtered_xml = self.xml_list[:]
            self.root.after(0, self.update_xml_listbox)
            self.log(f"Đã lấy {len(self.xml_list)} file XML.")
        except Exception as e:
            self.log(f"Lỗi tải danh sách XML: {e}")

    def update_xml_listbox(self):
        self.lb_xml.delete(0, END)
        for f in self.filtered_xml:
            self.lb_xml.insert(END, f["name"])

    def _on_search_var_changed(self, *args):
        if self._search_after_id:
            self.root.after_cancel(self._search_after_id)
        self._search_after_id = self.root.after(self.SEARCH_DEBOUNCE_MS, self.filter_xml_list)

    def filter_xml_list(self):
        kw = self.search_var.get().lower().strip()
        self.filtered_xml = [f for f in self.xml_list if kw in f["name"].lower()] if kw else self.xml_list[:]
        self.update_xml_listbox()
        self.log(f"Lọc '{kw}': còn {len(self.filtered_xml)} file.")
        if len(self.filtered_xml) == 1:
            self.lb_xml.selection_set(0)
            threading.Thread(target=self.parse_xml, args=(self.filtered_xml[0],), daemon=True).start()

    def reset_xml_list(self):
        self.filtered_xml = self.xml_list[:]
        self.update_xml_listbox()

    def on_xml_select(self, event):
        if sel := self.lb_xml.curselection():
            threading.Thread(target=self.parse_xml, args=(self.filtered_xml[sel[0]],), daemon=True).start()

    def parse_xml(self, f):
        if not (url := f.get("download_url")):
            self.log(f"Lỗi: download_url không có cho {f.get('name')}")
            return
        self.current_xml_file = f["name"]
        self.log(f"Tải & parse XML: {f['name']} ...")
        try:
            text = fetch_text(url)
            self.games = parse_xml_games(
                text,
                skip_keywords=self.skip_keywords.get(),
                include_clones=self.include_clones.get()
            )
            self.log(f"Parse xong {len(self.games)} game từ {f['name']}.")
        except Exception as e:
            self.log(f"Lỗi parse XML: {e}")

    # -----------------------
    # Copy files
    # -----------------------
    def threaded_copy(self):
        threading.Thread(target=self.copy_files, daemon=True).start()

    def copy_files(self):
        if not self.games:
            messagebox.showerror("Lỗi", "Chưa chọn file XML hoặc chưa parse xong.")
            return

        src, dst_root = self.source_dir.get(), self.dest_dir.get()

        # Thư mục nguồn bắt buộc phải tồn tại
        if not os.path.isdir(src):
            messagebox.showerror("Lỗi", "Thư mục nguồn không hợp lệ hoặc không tồn tại.")
            return

        # Nếu thư mục đích chưa tồn tại -> tự tạo
        if not os.path.exists(dst_root):
            try:
                os.makedirs(dst_root, exist_ok=True)
                self.log(f"Đã tạo thư mục đích: {dst_root}")
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể tạo thư mục đích: {e}")
                return

        # Parse extensions
        ext_text = self.extensions.get().strip()
        extensions = None
        if ext_text:
            extensions = set(ext.lower() for ext in ext_text.split(",") if ext.strip())
            extensions = {ext if ext.startswith(".") else f".{ext}" for ext in extensions}

        processor = CopyOnlyProcessor(src, dst_root, self.current_xml_file, self.games, extensions)
        success, total = processor.process(
            progress_callback=self.update_progress,
            log_callback=self.log
        )
        self.log(f"Xong! Đã copy {success}/{total} file vào thư mục {os.path.splitext(self.current_xml_file)[0]}.")

def main():
    root = Tk()
    app = CopyParentApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
