import os, sys, re, shutil, threading, urllib.request, json, xml.etree.ElementTree as ET
from tkinter import Tk, Frame, Label, Button, Listbox, Scrollbar, Text, END, filedialog, StringVar, Entry, messagebox, ttk
from concurrent.futures import ThreadPoolExecutor, as_completed

API_URL = "https://api.github.com/repos/mamedev/mame/contents/hash"

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
            threading.Event().wait(1)  # Wait before retry

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

def parse_xml_softwares(xml_text):
    try:
        root = ET.fromstring(xml_text)
        return [{"name": sw.get("name", "").strip(),
                 "description": (sw.findtext("description") or "").strip()}
                for sw in root.iter("software")]
    except ET.ParseError:
        return []

def build_normalized_file_map(files, extensions=None):
    if extensions:
        files = [f for f in files if os.path.splitext(f)[1].lower() in extensions]
    return {normalize_text(os.path.splitext(os.path.basename(f))[0]): f for f in files}

def find_match_fast(desc, file_map):
    desc_norm = normalize_text(desc)
    for fname_norm, path in file_map.items():
        if desc_norm in fname_norm:
            return path
    return None

# -----------------------
# Core Logic (tách riêng để dễ testing)
# -----------------------
class CopyRenameProcessor:
    def __init__(self, source_dir, dest_dir, xml_file, items, extensions=None):
        self.source_dir = source_dir
        self.dest_dir = dest_dir
        self.xml_file = xml_file
        self.items = items
        self.extensions = extensions
        self.copied_count = 0
        self.lock = threading.Lock()
        
    def process(self, progress_callback=None, log_callback=None):
        dst = os.path.join(self.dest_dir, os.path.splitext(os.path.basename(self.xml_file))[0]) if self.xml_file else self.dest_dir
        os.makedirs(dst, exist_ok=True)

        files = [os.path.join(self.source_dir, f) for f in os.listdir(self.source_dir) if os.path.isfile(os.path.join(self.source_dir, f))]
        if not files:
            if log_callback:
                log_callback("Không có file trong thư mục nguồn.")
            return 0, 0

        file_map = build_normalized_file_map(files, self.extensions)
        if log_callback:
            log_callback(f"Bắt đầu copy đa luồng vào thư mục: {dst}")

        results = []
        total = len(self.items)
        
        with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as exe:
            futures = {exe.submit(self.process_item, item, dst, file_map): i for i, item in enumerate(self.items)}
            
            for i, future in enumerate(as_completed(futures)):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(f"[ERR FUT] {e}")
                
                if progress_callback:
                    progress_callback(i + 1, total)
                
                if log_callback:
                    log_callback(result)
        
        success_count = sum(1 for r in results if r.startswith("[OK]"))
        return success_count, total

    def process_item(self, item, dst, file_map):
        match = find_match_fast(item.get("description"), file_map)
        if not match:
            return f"[MISS] {item.get('name')} - {item.get('description')}"
            
        dst_path = os.path.join(dst, item["name"] + os.path.splitext(match)[1])
        if os.path.exists(dst_path):
            return f"[SKIP] {os.path.basename(dst_path)} (đã có)"
            
        try:
            shutil.copy2(match, dst_path)
            with self.lock:
                self.copied_count += 1
            return f"[OK] {os.path.basename(match)} -> {os.path.basename(dst_path)}"
        except Exception as e:
            return f"[ERR] {match}: {e}"

# -----------------------
# UI
# -----------------------
class CopyRenameApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Copy & Rename từ MAME hash XML")
        self.root.geometry("600x600")
        self.root.minsize(500, 500)

        self.source_dir = StringVar()
        self.dest_dir = StringVar(value=os.path.dirname(os.path.abspath(sys.argv[0])))
        self.extensions = StringVar(value=".zip,.7z,.rar")  # Filter phần mở rộng

        self.xml_list = []
        self.filtered_xml = []
        self.items = []
        self.current_xml_file = None

        self._search_after_id = None
        self.SEARCH_DEBOUNCE_MS = 300
        
        self.progress = None
        self.progress_label = None

        self.build_ui()
        self.threaded_fetch_xml_list()

    def build_ui(self):
        # Main container với padding
        main_frame = Frame(self.root, padx=10, pady=10)
        main_frame.pack(fill="both", expand=True)
        
        # === Top frame: search + list XML ===
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

        # === Middle frame: source/dest + options ===
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
        Label(mid_frame, text="(cách nhau bằng dấu phẩy)").grid(row=2, column=2, sticky="w", padx=4)

        # Progress bar
        progress_frame = Frame(mid_frame)
        progress_frame.grid(row=3, column=0, columnspan=3, sticky="we", pady=8)
        self.progress_label = Label(progress_frame, text="Sẵn sàng")
        self.progress_label.pack(side="top", fill="x")
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="top", fill="x")

        Button(mid_frame, text="Copy + Rename", command=self.threaded_copy, 
               bg="#4CAF50", fg="white", width=15).grid(row=4, column=1, pady=8)

        # === Bottom frame: logs ===
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
            self.items = parse_xml_softwares(text)
            self.log(f"Parse xong {len(self.items)} mục từ {f['name']}.")
        except Exception as e:
            self.log(f"Lỗi parse XML: {e}")

    def threaded_copy(self):
        threading.Thread(target=self.copy_files, daemon=True).start()

    def copy_files(self):
        if not self.items:
            messagebox.showerror("Lỗi", "Chưa chọn file XML hoặc chưa parse xong.")
            return
            
        src, dst_root = self.source_dir.get(), self.dest_dir.get()
        if not all(os.path.isdir(d) for d in [src, dst_root]):
            messagebox.showerror("Lỗi", "Thư mục nguồn/đích không hợp lệ.")
            return

        # Parse extensions filter
        ext_text = self.extensions.get().strip()
        extensions = None
        if ext_text:
            extensions = set(ext.lower() for ext in ext_text.split(",") if ext.strip())
            extensions = {ext if ext.startswith(".") else f".{ext}" for ext in extensions}

        processor = CopyRenameProcessor(src, dst_root, self.current_xml_file, self.items, extensions)
        success, total = processor.process(
            progress_callback=self.update_progress,
            log_callback=self.log
        )
        
        self.log(f"Xong! Đã copy {success}/{total} file.")

def main():
    root = Tk()
    app = CopyRenameApp(root)
    root.mainloop()

if __name__ == "__main__": main()