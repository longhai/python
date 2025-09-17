import urllib.request
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from xml.etree import ElementTree
import io
import concurrent.futures
import threading
import time
from datetime import timedelta

class DownloaderApp:
    GITHUB_HASH_API = "https://api.github.com/repos/mamedev/mame/contents/hash"

    def __init__(self, root):
        self.root = root
        self.root.title("📥 Tải ảnh từ XML (MAME Hash)")
        self.root.geometry("900x560")
        self.root.resizable(False, False)
        self.root.configure(bg="#f5f6f5")

        # ====== STATE ======
        self.output_dir = os.path.dirname(os.path.abspath(__file__))  # mặc định thư mục chứa file .py
        self.cancel_event = threading.Event()
        self.executor = None
        self.total_tasks = 0
        self.completed_tasks = 0
        self.start_time = None
        self.retries = 3
        self.timeout = 20

        self.platform_media_base = {
            "nes": "http://adb.arcadeitalia.net/media/mess.current/ingames/nes/",
            "snes": "http://adb.arcadeitalia.net/media/mess.current/ingames/snes/",
        }

        # ====== STYLES ======
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Accent.TButton", foreground="#ffffff", background="#4CAF50")
        style.map("Accent.TButton", background=[("active", "#45a049")])
        style.configure("Danger.TButton", foreground="#ffffff", background="#f44336")
        style.map("Danger.TButton", background=[("active", "#d32f2f")])
        style.configure("TLabel", font=("Segoe UI", 10), background="#f5f6f5")
        style.configure("TCombobox", font=("Segoe UI", 10))
        style.configure("TSpinbox", font=("Segoe UI", 10))
        style.configure("TProgressbar", thickness=18, troughcolor="#e0e0e0", background="#4CAF50")

        # ====== HEADER ======
        header = tk.Canvas(root, height=50, bg="#2196F3", highlightthickness=0)
        header.pack(fill="x")
        header.create_rectangle(0, 0, 900, 50, fill="#2196F3", outline="")
        header.create_text(450, 25, text="📥 Tải ảnh từ XML (đa luồng, hủy, log, ETA)", font=("Segoe UI", 14, "bold"), fill="white")

        # ====== MAIN FRAME ======
        main = tk.Frame(root, bg="#f5f6f5")
        main.pack(fill="both", expand=True, padx=12, pady=10)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=1)

        # ---- Chọn thư mục ----
        folder_frame = tk.Frame(main, bg="#f5f6f5")
        folder_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0,6))
        ttk.Label(folder_frame, text="Thư mục lưu:").grid(row=0, column=0, sticky="w")
        self.folder_label = ttk.Label(folder_frame, text=self.output_dir)
        self.folder_label.grid(row=0, column=1, sticky="w", padx=(6,6))
        ttk.Button(folder_frame, text="📂 Chọn", style="TButton", command=self.choose_folder).grid(row=0, column=2, sticky="e")

        # ---- Cột trái: danh sách hệ máy với scrollbar ----
        left = tk.LabelFrame(main, text="Hệ máy (chọn nhiều)", bg="#f5f6f5")
        left.grid(row=1, column=0, sticky="nsew", padx=(0,6))
        left.configure(labelanchor="n")

        platform_frame = tk.Frame(left, bg="#f5f6f5")
        platform_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self.platform_list = tk.Listbox(platform_frame, selectmode=tk.EXTENDED, height=14, activestyle="dotbox")
        self.platform_list.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(platform_frame, orient="vertical", command=self.platform_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.platform_list.config(yscrollcommand=scrollbar.set)

        btns_left = tk.Frame(left, bg="#f5f6f5")
        btns_left.pack(fill="x", padx=8, pady=(0,8))
        ttk.Button(btns_left, text="🔄 Nạp danh sách từ GitHub", command=self.load_platforms).pack(side="left")

        # ---- Cột giữa: tùy chọn ----
        middle = tk.LabelFrame(main, text="Tùy chọn", bg="#f5f6f5")
        middle.grid(row=1, column=1, sticky="nsew", padx=6)
        middle.configure(labelanchor="n")

        rowi = 0
        ttk.Label(middle, text="Số luồng:").grid(row=rowi, column=0, sticky="w", padx=8, pady=6)
        self.thread_var = tk.IntVar(value=16)
        ttk.Spinbox(middle, from_=1, to=64, textvariable=self.thread_var, width=6).grid(row=rowi, column=1, sticky="w")
        rowi += 1

        ttk.Label(middle, text="Số lần thử lại (retry):").grid(row=rowi, column=0, sticky="w", padx=8, pady=6)
        self.retry_var = tk.IntVar(value=3)
        ttk.Spinbox(middle, from_=0, to=10, textvariable=self.retry_var, width=6).grid(row=rowi, column=1, sticky="w")
        rowi += 1

        ttk.Label(middle, text="Timeout (giây):").grid(row=rowi, column=0, sticky="w", padx=8, pady=6)
        self.timeout_var = tk.IntVar(value=20)
        ttk.Spinbox(middle, from_=5, to=120, textvariable=self.timeout_var, width=6).grid(row=rowi, column=1, sticky="w")
        rowi += 1

        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(middle, text="Tải lại (ghi đè nếu tồn tại)", variable=self.force_var).grid(row=rowi, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        rowi += 1

        # ---- Cột phải: điều khiển ----
        right = tk.LabelFrame(main, text="Điều khiển", bg="#f5f6f5")
        right.grid(row=1, column=2, sticky="nsew", padx=(6,0))
        right.configure(labelanchor="n")

        self.start_btn = ttk.Button(right, text="⬇️ Bắt đầu tải", style="Accent.TButton", command=self.start_download)
        self.start_btn.pack(fill="x", padx=8, pady=(8,6))
        self.cancel_btn = ttk.Button(right, text="⏹️ Hủy", style="Danger.TButton", command=self.cancel_download, state=tk.DISABLED)
        self.cancel_btn.pack(fill="x", padx=8, pady=(0,8))

        self.status_label = ttk.Label(right, text="Chờ bắt đầu…")
        self.status_label.pack(fill="x", padx=8, pady=4)
        self.eta_label = ttk.Label(right, text="ETA: —")
        self.eta_label.pack(fill="x", padx=8, pady=(0,8))

        self.progress = ttk.Progressbar(right, orient="horizontal", mode="determinate", length=260)
        self.progress.pack(fill="x", padx=8, pady=(0,8))

        # ---- Log ----
        log_frame = tk.LabelFrame(main, text="Nhật ký", bg="#f5f6f5")
        log_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(8,0))
        log_frame.configure(labelanchor="n")

        self.log_text = tk.Text(log_frame, height=10, wrap="word", state=tk.DISABLED)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8,0), pady=8)
        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)

    # ====== UI Helpers ======
    def choose_folder(self):
        directory = filedialog.askdirectory()
        if directory:
            self.output_dir = directory
            self.folder_label.config(text=self.output_dir)

    def load_platforms(self):
        def _task():
            try:
                opener = urllib.request.build_opener()
                opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
                with opener.open(self.GITHUB_HASH_API, timeout=20) as resp:
                    import json
                    data = json.loads(resp.read().decode('utf-8'))
                xmls = [item['name'] for item in data if item.get('name','').endswith('.xml')]
                xmls.sort()
                self.root.after(0, lambda: self._populate_platforms(xmls))
                self.log(f"Nạp {len(xmls)} file XML từ GitHub thành công.")
            except Exception as e:
                self.log(f"Không thể nạp danh sách từ GitHub: {e}")
        threading.Thread(target=_task, daemon=True).start()

    def _populate_platforms(self, xml_list):
        self.platform_list.delete(0, tk.END)
        for name in xml_list:
            self.platform_list.insert(tk.END, name)

    def log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def set_controls_running(self, running: bool):
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.cancel_btn.config(state=tk.NORMAL if running else tk.DISABLED)

    # ====== Networking / Parsing ======
    def get_image_urls(self, platform_xml_name: str):
        xml_url = f"https://raw.githubusercontent.com/mamedev/mame/refs/heads/master/hash/{platform_xml_name}"
        xml_filename = os.path.basename(xml_url)
        xml_name = os.path.splitext(xml_filename)[0]

        base_url = self.platform_media_base.get(xml_name, f"http://adb.arcadeitalia.net/media/mess.current/ingames/{xml_name}/")

        try:
            opener = urllib.request.build_opener()
            opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
            with opener.open(xml_url, timeout=self.timeout_var.get()) as response:
                xml_content = response.read()
            tree = ElementTree.parse(io.BytesIO(xml_content))
            root = tree.getroot()
            image_urls = []
            for sw in root.findall("software"):
                name = sw.get("name")
                if name:
                    image_urls.append(f"{base_url}{name}.png")
            return xml_name, base_url, image_urls
        except Exception as e:
            self.log(f"[LỖI] Không thể tải/đọc {platform_xml_name}: {e}")
            return xml_name, base_url, []

    def download_file(self, url: str, save_dir: str, force: bool, retries: int, timeout: int):
        if self.cancel_event.is_set():
            return "Đã hủy"
        filename = os.path.join(save_dir, url.split("/")[-1])
        if (not force) and os.path.exists(filename):
            return f"Bỏ qua (tồn tại): {os.path.basename(filename)}"

        for attempt in range(1, retries + 1):
            if self.cancel_event.is_set():
                return "Đã hủy"
            try:
                opener = urllib.request.build_opener()
                opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
                with opener.open(url, timeout=timeout) as resp:
                    data = resp.read()
                with open(filename, 'wb') as f:
                    f.write(data)
                return f"OK: {os.path.basename(filename)}"
            except Exception as e:
                if attempt < retries:
                    time.sleep(1.0 * attempt)
                else:
                    return f"Lỗi: {os.path.basename(filename)} -> {e}"

    # ====== Download Flow ======
    def start_download(self):
        if not self.output_dir:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng chọn thư mục lưu trước.")
            return
        selected = [self.platform_list.get(i) for i in self.platform_list.curselection()]
        if not selected:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng chọn ít nhất một hệ máy (XML).")
            return

        self.cancel_event.clear()
        self.set_controls_running(True)
        self.progress.config(value=0, maximum=100)
        self.status_label.config(text="Đang chuẩn bị…")
        self.eta_label.config(text="ETA: —")
        self.log("===== BẮT ĐẦU =====")

        threading.Thread(target=self._download_task, args=(selected,), daemon=True).start()

    def cancel_download(self):
        self.cancel_event.set()
        self.log("Yêu cầu hủy tải…")

    def _update_progress_ui(self):
        pct = (self.completed_tasks / self.total_tasks) * 100.0 if self.total_tasks > 0 else 0.0
        self.progress.config(value=pct, maximum=100)
        self.status_label.config(text=f"Đã xử lý {self.completed_tasks}/{self.total_tasks} ảnh")

        if self.start_time and self.completed_tasks > 0:
            elapsed = time.time() - self.start_time
            speed = self.completed_tasks / elapsed
            remaining = self.total_tasks - self.completed_tasks
            eta_seconds = remaining / speed if speed > 0 else 0
            self.eta_label.config(text=f"ETA: {str(timedelta(seconds=int(eta_seconds)))}")
        else:
            self.eta_label.config(text="ETA: —")

    def _download_task(self, selected_xmls):
        force = self.force_var.get()
        retries = self.retry_var.get()
        timeout = self.timeout_var.get()
        max_workers = self.thread_var.get()

        all_jobs = []
        for platform_xml in selected_xmls:
            if self.cancel_event.is_set():
                break
            xml_name, base_url, image_urls = self.get_image_urls(platform_xml)
            if not image_urls:
                self.log(f"[BỎ QUA] {platform_xml}: không lấy được URL ảnh.")
                continue
            save_dir = os.path.join(self.output_dir, xml_name)
            os.makedirs(save_dir, exist_ok=True)
            for url in image_urls:
                all_jobs.append((xml_name, save_dir, url))
            self.log(f"{platform_xml}: {len(image_urls)} ảnh | base: {base_url}")

        self.total_tasks = len(all_jobs)
        self.completed_tasks = 0
        self.start_time = time.time()
        self.root.after(0, self._update_progress_ui)

        if self.total_tasks == 0:
            self.root.after(0, lambda: [
                self.set_controls_running(False),
                self.status_label.config(text="Không có tác vụ."),
                self.log("Không có URL để tải."),
            ])
            return

        master_log_path = os.path.join(self.output_dir, "download.log")
        try:
            log_f = open(master_log_path, "a", encoding="utf-8")
            log_f.write("===== BẮT ĐẦU =====\n")
        except Exception:
            log_f = None

        def on_done(future, platform):
            res = future.result()
            if res:
                self.root.after(0, lambda: self.log(f"[{platform}] {res}"))
                if log_f:
                    try:
                        log_f.write(f"[{platform}] {res}\n")
                    except Exception:
                        pass
            self.completed_tasks += 1
            self.root.after(0, self._update_progress_ui)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = []
                for platform, save_dir, url in all_jobs:
                    if self.cancel_event.is_set():
                        break
                    fut = ex.submit(self.download_file, url, save_dir, force, retries, timeout)
                    fut.add_done_callback(lambda f, p=platform: on_done(f, p))
                    futures.append(fut)
                for fut in concurrent.futures.as_completed(futures):
                    if self.cancel_event.is_set():
                        break
        except Exception as e:
            self.root.after(0, lambda: self.log(f"[LỖI] Executor: {e}"))
        finally:
            if log_f:
                try:
                    log_f.write("===== KẾT THÚC =====\n")
                    log_f.close()
                except Exception:
                    pass

        def _finish():
            self.set_controls_running(False)
            if self.cancel_event.is_set():
                self.status_label.config(text=f"⏹️ Đã hủy: {self.completed_tasks}/{self.total_tasks}")
                self.log("⏹️ ĐÃ HỦY")
                messagebox.showinfo("Đã hủy", f"Đã xử lý {self.completed_tasks}/{self.total_tasks} ảnh trước khi hủy.")
            else:
                self.status_label.config(text=f"✅ Hoàn tất: {self.completed_tasks}/{self.total_tasks}")
                self.log("✅ HOÀN TẤT")
                messagebox.showinfo("Kết quả", f"Hoàn tất tải {self.completed_tasks}/{self.total_tasks} ảnh.\nLog: {master_log_path}")
        self.root.after(0, _finish)

def main():
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
