"""
CareS - 护眼/久坐/喝水提醒助手
功能：定时提醒、内存清理、系统托盘
"""

import customtkinter as ctk
import threading
import time
import ctypes
import ctypes.wintypes as wintypes
import winsound
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item
import sys

# 设置外观
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def is_admin():
    """检查是否有管理员权限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def elevate_privileges():
    """尝试提升权限"""
    try:
        # 获取当前进程令牌
        token = wintypes.HANDLE()
        ctypes.windll.advapi32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(),
            0x0020 | 0x0008,  # TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY
            ctypes.byref(token)
        )
        
        # 查找 SeIncreaseQuotaPrivilege
        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]
        
        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]
        
        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]
        
        luid = LUID()
        ctypes.windll.advapi32.LookupPrivilegeValueW(None, "SeIncreaseQuotaPrivilege", ctypes.byref(luid))
        
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED
        
        ctypes.windll.advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        ctypes.windll.kernel32.CloseHandle(token)
        return True
    except:
        return False


class SYSTEM_MEMORY_LIST_COMMAND:
    MemoryEmptyWorkingSets = 0
    MemoryFlushModifiedList = 1
    MemoryPurgeStandbyList = 2
    MemoryPurgeLowPriorityStandbyList = 3


class SYSTEM_FILECACHE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("CurrentSize", ctypes.c_size_t),
        ("PeakSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("MinimumWorkingSet", ctypes.c_size_t),
        ("MaximumWorkingSet", ctypes.c_size_t),
        ("CurrentSizeIncludingTransitionInPages", ctypes.c_size_t),
        ("PeakSizeIncludingTransitionInPages", ctypes.c_size_t),
        ("TransitionRePurposeCount", wintypes.ULONG),
        ("Flags", wintypes.ULONG),
    ]


def clean_memory_native():
    """使用 Native API 清理内存（类似 memreduct）"""
    ntdll = ctypes.windll.ntdll
    kernel32 = ctypes.windll.kernel32
    
    results = {}
    
    # 获取初始内存
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    before_free = stat.ullAvailPhys
    
    # 提升权限
    elevate_privileges()
    
    # 1. 清空所有进程工作集
    try:
        status = ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_COMMAND.MemoryEmptyWorkingSets,
            ctypes.byref(ctypes.c_ulong(0)),
            ctypes.sizeof(ctypes.c_ulong)
        )
        results["工作集"] = status == 0
    except:
        results["工作集"] = False
    
    # 2. 清理待机列表
    try:
        status = ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_COMMAND.MemoryPurgeStandbyList,
            ctypes.byref(ctypes.c_ulong(0)),
            ctypes.sizeof(ctypes.c_ulong)
        )
        results["待机列表"] = status == 0
    except:
        results["待机列表"] = False
    
    # 3. 清理低优先级待机列表
    try:
        status = ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_COMMAND.MemoryPurgeLowPriorityStandbyList,
            ctypes.byref(ctypes.c_ulong(0)),
            ctypes.sizeof(ctypes.c_ulong)
        )
        results["低优先级待机"] = status == 0
    except:
        results["低优先级待机"] = False
    
    # 4. 刷新修改页面列表
    try:
        status = ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_COMMAND.MemoryFlushModifiedList,
            ctypes.byref(ctypes.c_ulong(0)),
            ctypes.sizeof(ctypes.c_ulong)
        )
        results["修改页面"] = status == 0
    except:
        results["修改页面"] = False
    
    # 5. 清理系统文件缓存
    try:
        sfci = SYSTEM_FILECACHE_INFORMATION()
        sfci.MinimumWorkingSet = ctypes.c_size_t(-1).value
        sfci.MaximumWorkingSet = ctypes.c_size_t(-1).value
        status = ntdll.NtSetSystemInformation(
            31,  # SystemFileCacheInformationEx
            ctypes.byref(sfci),
            ctypes.sizeof(sfci)
        )
        results["文件缓存"] = status == 0
    except:
        results["文件缓存"] = False
    
    # 获取清理后内存
    kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    after_free = stat.ullAvailPhys
    
    released = max(0, (after_free - before_free) / (1024 * 1024))
    total_free = after_free / (1024 * 1024)
    
    return released, total_free, results


class CareSApp:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("CareS - 健康提醒助手")
        self.root.geometry("500x650")
        self.root.resizable(False, False)
        
        # 提醒状态
        self.reminders = {
            "eye": {"name": "护眼提醒", "enabled": False, "interval": 30, "count": 0, "remaining": 0, "timer": None, "next_time": 0},
            "sit": {"name": "久坐提醒", "enabled": False, "interval": 40, "count": 0, "remaining": 0, "timer": None, "next_time": 0},
            "water": {"name": "喝水提醒", "enabled": False, "interval": 30, "count": 0, "remaining": 0, "timer": None, "next_time": 0},
        }
        
        self.tray_icon = None
        self.is_running = True
        self.update_tray_timer = None
        
        # 初始化系统通知
        self.init_toast()
        
        self.create_ui()
        self.setup_tray()
        self.start_tray_updater()
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
    
    def init_toast(self):
        """初始化Windows通知"""
        try:
            from win10toast_persist import ToastNotifier
            self.toaster = ToastNotifier()
            self.use_win10toast = True
        except:
            self.use_win10toast = False
    
    def create_ui(self):
        # 标题栏
        title_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        title_frame.pack(pady=(20, 10), padx=20, fill="x")
        
        ctk.CTkLabel(title_frame, text="CareS", font=ctk.CTkFont(size=28, weight="bold")).pack(side="left")
        ctk.CTkLabel(title_frame, text="健康提醒助手", font=ctk.CTkFont(size=14), text_color="gray").pack(side="left", padx=(10, 0), pady=(8, 0))
        
        # 内存清理按钮
        admin_text = "🧹 清理内存" if is_admin() else "🧹 清理内存(需管理员)"
        ctk.CTkButton(title_frame, text=admin_text, width=120, height=32, command=self.clean_memory).pack(side="right")
        
        # 分隔线
        ctk.CTkFrame(self.root, height=2, fg_color="gray75").pack(padx=20, fill="x", pady=10)
        
        # 三个提醒卡片
        for key in ["eye", "sit", "water"]:
            self.create_reminder_card(key)
        
        # 底部状态
        self.status_label = ctk.CTkLabel(self.root, text="程序运行中，关闭窗口将最小化到托盘", font=ctk.CTkFont(size=11), text_color="gray")
        self.status_label.pack(side="bottom", pady=(10, 15))
    
    def create_reminder_card(self, key):
        reminder = self.reminders[key]
        icons = {"eye": "👁️", "sit": "🪑", "water": "💧"}
        
        card = ctk.CTkFrame(self.root, corner_radius=10)
        card.pack(padx=20, pady=8, fill="x")
        
        top_frame = ctk.CTkFrame(card, fg_color="transparent")
        top_frame.pack(padx=15, pady=(12, 5), fill="x")
        
        ctk.CTkLabel(top_frame, text=f"{icons[key]} {reminder['name']}", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        
        # 次数选择
        count_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        count_frame.pack(side="right", padx=(0, 10))
        
        ctk.CTkLabel(count_frame, text="次数:", font=ctk.CTkFont(size=12)).pack(side="left")
        count_combo = ctk.CTkComboBox(
            count_frame, 
            values=["无限", "1次", "2次", "3次", "5次", "10次"],
            width=70,
            height=28,
            font=ctk.CTkFont(size=11),
            command=lambda v, k=key: self.set_count(k, v)
        )
        count_combo.set("无限")
        count_combo.pack(side="left", padx=5)
        reminder["count_combo"] = count_combo
        
        # 开关
        switch = ctk.CTkSwitch(top_frame, text="", command=lambda k=key: self.toggle_reminder(k), onvalue=True, offvalue=False)
        switch.pack(side="right")
        reminder["switch"] = switch
        
        # 间隔设置
        interval_frame = ctk.CTkFrame(card, fg_color="transparent")
        interval_frame.pack(padx=15, pady=5, fill="x")
        
        ctk.CTkLabel(interval_frame, text="提醒间隔：", font=ctk.CTkFont(size=13)).pack(side="left")
        
        for mins in [10, 20, 30, 40, 50, 60]:
            btn = ctk.CTkButton(interval_frame, text=f"{mins}分", width=45, height=28, font=ctk.CTkFont(size=11), command=lambda k=key, m=mins: self.set_interval(k, m))
            btn.pack(side="left", padx=2)
            if mins == reminder["interval"]:
                btn.configure(fg_color="#2563eb", hover_color="#1d4ed8")
        
        # 自定义输入
        custom_frame = ctk.CTkFrame(card, fg_color="transparent")
        custom_frame.pack(padx=15, pady=(5, 12), fill="x")
        
        ctk.CTkLabel(custom_frame, text="自定义：", font=ctk.CTkFont(size=13)).pack(side="left")
        
        def validate_number(P):
            return P == "" or P.isdigit()
        
        vcmd = (self.root.register(validate_number), '%P')
        custom_entry = ctk.CTkEntry(custom_frame, width=60, height=28, placeholder_text="分钟", font=ctk.CTkFont(size=12), validate="key", validatecommand=vcmd)
        custom_entry.pack(side="left", padx=5)
        
        ctk.CTkButton(custom_frame, text="设置", width=50, height=28, font=ctk.CTkFont(size=11), command=lambda k=key, e=custom_entry: self.set_custom_interval(k, e)).pack(side="left")
        
        status_label = ctk.CTkLabel(custom_frame, text="已禁用", font=ctk.CTkFont(size=11), text_color="gray")
        status_label.pack(side="right")
        reminder["status_label"] = status_label
    
    def set_count(self, key, value):
        if value == "无限":
            self.reminders[key]["count"] = 0
            self.reminders[key]["remaining"] = 0
        else:
            count = int(value.replace("次", ""))
            self.reminders[key]["count"] = count
            self.reminders[key]["remaining"] = count
        
        if self.reminders[key]["enabled"]:
            self.stop_reminder(key)
            self.start_reminder(key)
    
    def toggle_reminder(self, key):
        self.reminders[key]["enabled"] = not self.reminders[key]["enabled"]
        if self.reminders[key]["enabled"]:
            if self.reminders[key]["count"] > 0:
                self.reminders[key]["remaining"] = self.reminders[key]["count"]
            self.start_reminder(key)
            self.reminders[key]["status_label"].configure(text="已启用", text_color="#2563eb")
        else:
            self.stop_reminder(key)
            self.reminders[key]["status_label"].configure(text="已禁用", text_color="gray")
    
    def set_interval(self, key, minutes):
        if minutes > 60:
            minutes = 60
        self.reminders[key]["interval"] = minutes
        
        if not self.reminders[key]["enabled"]:
            self.reminders[key]["switch"].toggle()
        
        self.stop_reminder(key)
        self.start_reminder(key)
        
        self.show_toast(f"{self.reminders[key]['name']}已设置为{minutes}分钟")
    
    def set_custom_interval(self, key, entry):
        try:
            value = entry.get()
            if not value:
                return
            minutes = int(value)
            if minutes < 1:
                minutes = 1
            elif minutes > 60:
                minutes = 60
            self.set_interval(key, minutes)
        except ValueError:
            pass
    
    def start_reminder(self, key):
        reminder = self.reminders[key]
        if reminder["timer"]:
            reminder["timer"].cancel()
        
        interval = reminder["interval"] * 60
        reminder["next_time"] = time.time() + interval
        
        def timer_callback():
            if self.is_running and reminder["enabled"]:
                if reminder["count"] > 0:
                    reminder["remaining"] -= 1
                    if reminder["remaining"] <= 0:
                        self.root.after(0, lambda: self.reminders[key]["switch"].toggle())
                        return
                
                self.show_reminder(key)
                
                if reminder["count"] == 0 or reminder["remaining"] > 0:
                    reminder["timer"] = threading.Timer(interval, timer_callback)
                    reminder["timer"].daemon = True
                    reminder["timer"].start()
                    reminder["next_time"] = time.time() + interval
        
        reminder["timer"] = threading.Timer(interval, timer_callback)
        reminder["timer"].daemon = True
        reminder["timer"].start()
    
    def stop_reminder(self, key):
        if self.reminders[key]["timer"]:
            self.reminders[key]["timer"].cancel()
            self.reminders[key]["timer"] = None
        self.reminders[key]["next_time"] = 0
    
    def get_next_reminder(self):
        min_time = float('inf')
        next_key = None
        
        for key, reminder in self.reminders.items():
            if reminder["enabled"] and reminder["next_time"] > 0:
                remaining = reminder["next_time"] - time.time()
                if remaining > 0 and remaining < min_time:
                    min_time = remaining
                    next_key = key
        
        if next_key:
            return next_key, int(min_time / 60) + 1
        return None, 0
    
    def get_active_count(self):
        return sum(1 for r in self.reminders.values() if r["enabled"])
    
    def show_reminder(self, key):
        reminder = self.reminders[key]
        messages = {
            "eye": "是时候休息一下眼睛了！\n看看远处，放松20秒",
            "sit": "坐太久啦！\n起来活动一下吧",
            "water": "记得喝水哦！\n保持身体水分充足",
        }
        icons = {"eye": "👁️", "sit": "🪑", "water": "💧"}
        
        # 播放提示音
        threading.Thread(target=self.play_sound, daemon=True).start()
        
        # 显示带进度条的弹窗
        self.root.after(0, lambda: self.show_progress_popup(
            reminder["name"], 
            icons[key], 
            messages[key]
        ))
    
    def play_sound(self):
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except:
            pass
    
    def show_progress_popup(self, title, icon, message):
        """显示带进度条的弹窗，3秒自动消失"""
        popup = ctk.CTkToplevel(self.root)
        popup.title("")
        popup.geometry("350x180")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.overrideredirect(True)
        
        # 右下角位置
        x = popup.winfo_screenwidth() - 370
        y = popup.winfo_screenheight() - 230
        popup.geometry(f"350x180+{x}+{y}")
        
        # 外框
        outer_frame = ctk.CTkFrame(popup, corner_radius=12, fg_color="#ffffff", border_width=1, border_color="#e0e0e0")
        outer_frame.pack(fill="both", expand=True, padx=6, pady=6)
        
        # 标题行
        title_frame = ctk.CTkFrame(outer_frame, fg_color="transparent")
        title_frame.pack(padx=20, pady=(15, 5), fill="x")
        
        ctk.CTkLabel(title_frame, text=f"{icon} {title}", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        
        # 倒计时标签
        countdown_label = ctk.CTkLabel(title_frame, text="3秒", font=ctk.CTkFont(size=12), text_color="#888888")
        countdown_label.pack(side="right")
        
        # 消息内容
        ctk.CTkLabel(outer_frame, text=message, font=ctk.CTkFont(size=13), text_color="#333333").pack(padx=20, pady=(5, 10))
        
        # 进度条
        progress = ctk.CTkProgressBar(outer_frame, width=310, height=8, corner_radius=4)
        progress.pack(padx=20, pady=(0, 15))
        progress.set(0)
        
        # 进度动画
        total_time = 3000  # 3秒
        interval = 50  # 每50ms更新一次
        steps = total_time // interval
        
        def animate(step=0):
            if step <= steps:
                progress.set(step / steps)
                remaining = 3 - int(step * interval / 1000)
                if remaining > 0:
                    countdown_label.configure(text=f"{remaining}秒")
                popup.after(interval, animate, step + 1)
            else:
                popup.destroy()
        
        animate()
    
    def show_toast(self, message):
        toast = ctk.CTkToplevel(self.root)
        toast.title("")
        toast.geometry("250x60")
        toast.resizable(False, False)
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)
        
        x = toast.winfo_screenwidth() - 270
        y = toast.winfo_screenheight() - 100
        toast.geometry(f"250x60+{x}+{y}")
        
        frame = ctk.CTkFrame(toast, corner_radius=8)
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        ctk.CTkLabel(frame, text=f"✓ {message}", font=ctk.CTkFont(size=13)).pack(pady=15)
        
        toast.after(2000, toast.destroy)
    
    def clean_memory(self):
        """清理系统内存"""
        if not is_admin():
            self.show_toast("⚠️ 请以管理员身份运行以清理内存")
            return
        
        # 在后台线程执行清理
        threading.Thread(target=self._do_clean_memory, daemon=True).start()
    
    def _do_clean_memory(self):
        """执行内存清理"""
        released, total_free, results = clean_memory_native()
        
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)
        
        if self.use_win10toast:
            msg = f"已释放 {released:.0f} MB\n可用内存: {total_free:.0f} MB\n清理项: {success_count}/{total_count}"
            def show_toast():
                try:
                    self.toaster.show_toast(title="🧹 内存清理完成", msg=msg, duration=5, threaded=False)
                except:
                    pass
            threading.Thread(target=show_toast, daemon=True).start()
        else:
            self.root.after(0, lambda: self.show_memory_result(released, total_free, success_count, total_count))
    
    def show_memory_result(self, released, total_free, success_count, total_count):
        """显示清理结果"""
        toast = ctk.CTkToplevel(self.root)
        toast.title("")
        toast.geometry("300x100")
        toast.resizable(False, False)
        toast.attributes("-topmost", True)
        toast.overrideredirect(True)
        
        x = toast.winfo_screenwidth() - 320
        y = toast.winfo_screenheight() - 140
        toast.geometry(f"300x100+{x}+{y}")
        
        frame = ctk.CTkFrame(toast, corner_radius=8)
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        ctk.CTkLabel(frame, text=f"🧹 已释放 {released:.0f} MB", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(12, 2))
        ctk.CTkLabel(frame, text=f"可用内存: {total_free:.0f} MB | 清理项: {success_count}/{total_count}", font=ctk.CTkFont(size=11), text_color="gray").pack()
        
        toast.after(3000, toast.destroy)
    
    def create_tray_icon(self, text=""):
        image = Image.new('RGBA', (64, 64), (74, 144, 217, 255))
        draw = ImageDraw.Draw(image)
        
        if text:
            try:
                font = ImageFont.truetype("arial.ttf", 28)
            except:
                font = ImageFont.load_default()
            
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (64 - text_width) // 2
            y = (64 - text_height) // 2 - 5
            draw.text((x, y), text, fill='white', font=font)
        else:
            try:
                font = ImageFont.truetype("arial.ttf", 32)
            except:
                font = ImageFont.load_default()
            draw.text((18, 12), "C", fill='white', font=font)
        
        return image
    
    def update_tray_display(self):
        if not self.is_running or not self.tray_icon:
            return
        
        active_count = self.get_active_count()
        next_key, next_minutes = self.get_next_reminder()
        
        if active_count > 0 and next_key:
            icon_text = str(next_minutes)
            tooltip = f"CareS - {active_count}个提醒运行中\n{self.reminders[next_key]['name']}: {next_minutes}分钟后提醒"
        else:
            icon_text = ""
            tooltip = "CareS - 健康提醒助手\n无提醒运行中"
        
        try:
            new_icon = self.create_tray_icon(icon_text)
            self.tray_icon.icon = new_icon
            self.tray_icon.title = tooltip
        except:
            pass
        
        self.update_tray_timer = threading.Timer(30, self.update_tray_display)
        self.update_tray_timer.daemon = True
        self.update_tray_timer.start()
    
    def start_tray_updater(self):
        self.update_tray_display()
    
    def setup_tray(self):
        icon_image = self.create_tray_icon()
        menu = pystray.Menu(
            item('显示主窗口', self.show_window, default=True),
            item('清理内存', self.clean_memory),
            item('退出', self.quit_app),
        )
        self.tray_icon = pystray.Icon("CareS", icon_image, "CareS - 健康提醒助手\n无提醒运行中", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
    
    def show_window(self):
        self.root.after(0, self._show_window_main)
    
    def _show_window_main(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def minimize_to_tray(self):
        self.root.withdraw()
    
    def quit_app(self):
        self.is_running = False
        if self.update_tray_timer:
            self.update_tray_timer.cancel()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
        sys.exit()
    
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = CareSApp()
    app.run()
