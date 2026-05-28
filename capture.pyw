import cv2
import os
import re
import sys
import time
import platform
import subprocess
from datetime import datetime


# ==========================
# 配置区域
# ==========================

MAX_CAMERA_INDEX = 10
WARMUP_TIME = 1.0
DISCARD_FRAMES = 10

# 是否优先使用 Windows DirectShow 后端
USE_DSHOW_ON_WINDOWS = True


def get_app_dir():
    """
    获取程序所在目录。
    兼容源码运行和 PyInstaller onefile exe。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def safe_filename(text, max_len=80):
    """
    清理文件名中的非法字符。
    """
    if not text:
        return "UnknownDevice"

    text = str(text).strip()
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    text = re.sub(r"\s+", "_", text)

    if len(text) > max_len:
        text = text[:max_len]

    return text or "UnknownDevice"


def fourcc_to_string(fourcc_value):
    """
    将 OpenCV 获取到的 FOURCC 编码数字转换为字符串。
    """
    try:
        fourcc_int = int(fourcc_value)
        chars = [
            chr((fourcc_int >> 0) & 0xFF),
            chr((fourcc_int >> 8) & 0xFF),
            chr((fourcc_int >> 16) & 0xFF),
            chr((fourcc_int >> 24) & 0xFF),
        ]
        return "".join(chars)
    except Exception:
        return "未知"


def safe_get(cap, prop_id):
    try:
        return cap.get(prop_id)
    except Exception:
        return None


def get_camera_names_by_pygrabber():
    """
    使用 pygrabber 获取 Windows DirectShow 摄像头设备名称。
    需要安装：
        pip install pygrabber comtypes

    返回：
        ["Integrated Camera", "USB Camera", ...]
    """
    try:
        from pygrabber.dshow_graph import FilterGraph

        graph = FilterGraph()
        devices = graph.get_input_devices()
        return list(devices)
    except Exception:
        return []


def get_camera_names_by_powershell():
    """
    使用 PowerShell 尝试获取摄像头相关设备名称。
    这个方法只能作为备用，不保证顺序和 OpenCV 索引完全对应。
    """
    if platform.system().lower() != "windows":
        return []

    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            r"""
            Get-CimInstance Win32_PnPEntity |
            Where-Object {
                $_.PNPClass -eq 'Camera' -or
                $_.PNPClass -eq 'Image' -or
                $_.Name -match 'Camera|Webcam|Video'
            } |
            Select-Object -ExpandProperty Name
            """
        ]

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="ignore"
        )

        names = []
        for line in completed.stdout.splitlines():
            line = line.strip()
            if line:
                names.append(line)

        return names

    except Exception:
        return []


def get_camera_device_names():
    """
    获取摄像头设备名称列表。
    优先 pygrabber，其次 PowerShell。
    """
    names = get_camera_names_by_pygrabber()

    if names:
        return {
            "source": "pygrabber / DirectShow",
            "names": names
        }

    names = get_camera_names_by_powershell()

    if names:
        return {
            "source": "PowerShell / Win32_PnPEntity，顺序可能不完全对应 OpenCV 索引",
            "names": names
        }

    return {
        "source": "未获取到设备名称，OpenCV 本身通常不提供设备名称",
        "names": []
    }


def get_camera_properties(cap):
    """
    获取摄像头详细属性。
    注意：不同摄像头和驱动支持的属性不同。
    """
    props = {
        "画面宽度 CAP_PROP_FRAME_WIDTH": safe_get(cap, cv2.CAP_PROP_FRAME_WIDTH),
        "画面高度 CAP_PROP_FRAME_HEIGHT": safe_get(cap, cv2.CAP_PROP_FRAME_HEIGHT),
        "FPS CAP_PROP_FPS": safe_get(cap, cv2.CAP_PROP_FPS),
        "FOURCC 编码 CAP_PROP_FOURCC": safe_get(cap, cv2.CAP_PROP_FOURCC),

        "亮度 CAP_PROP_BRIGHTNESS": safe_get(cap, cv2.CAP_PROP_BRIGHTNESS),
        "对比度 CAP_PROP_CONTRAST": safe_get(cap, cv2.CAP_PROP_CONTRAST),
        "饱和度 CAP_PROP_SATURATION": safe_get(cap, cv2.CAP_PROP_SATURATION),
        "色调 CAP_PROP_HUE": safe_get(cap, cv2.CAP_PROP_HUE),
        "增益 CAP_PROP_GAIN": safe_get(cap, cv2.CAP_PROP_GAIN),
        "曝光 CAP_PROP_EXPOSURE": safe_get(cap, cv2.CAP_PROP_EXPOSURE),

        "自动曝光 CAP_PROP_AUTO_EXPOSURE": safe_get(cap, cv2.CAP_PROP_AUTO_EXPOSURE),
        "自动白平衡 CAP_PROP_AUTO_WB": safe_get(cap, cv2.CAP_PROP_AUTO_WB),

        "白平衡蓝色U CAP_PROP_WHITE_BALANCE_BLUE_U": safe_get(
            cap, cv2.CAP_PROP_WHITE_BALANCE_BLUE_U
        ),
        "白平衡红色V CAP_PROP_WHITE_BALANCE_RED_V": safe_get(
            cap, cv2.CAP_PROP_WHITE_BALANCE_RED_V
        ),

        "伽马 CAP_PROP_GAMMA": safe_get(cap, cv2.CAP_PROP_GAMMA),
        "锐度 CAP_PROP_SHARPNESS": safe_get(cap, cv2.CAP_PROP_SHARPNESS),
        "背光补偿 CAP_PROP_BACKLIGHT": safe_get(cap, cv2.CAP_PROP_BACKLIGHT),

        "变焦 CAP_PROP_ZOOM": safe_get(cap, cv2.CAP_PROP_ZOOM),
        "焦点 CAP_PROP_FOCUS": safe_get(cap, cv2.CAP_PROP_FOCUS),
        "自动对焦 CAP_PROP_AUTOFOCUS": safe_get(cap, cv2.CAP_PROP_AUTOFOCUS),
        "光圈 CAP_PROP_IRIS": safe_get(cap, cv2.CAP_PROP_IRIS),

        "模式 CAP_PROP_MODE": safe_get(cap, cv2.CAP_PROP_MODE),
        "格式 CAP_PROP_FORMAT": safe_get(cap, cv2.CAP_PROP_FORMAT),
        "转换RGB CAP_PROP_CONVERT_RGB": safe_get(cap, cv2.CAP_PROP_CONVERT_RGB),
        "缓冲区大小 CAP_PROP_BUFFERSIZE": safe_get(cap, cv2.CAP_PROP_BUFFERSIZE),
    }

    return props


def open_camera(camera_index):
    """
    打开摄像头。
    Windows 下优先使用 DirectShow，这样和 pygrabber 设备枚举更接近。
    """
    if platform.system().lower() == "windows" and USE_DSHOW_ON_WINDOWS:
        return cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)

    return cv2.VideoCapture(camera_index)


def capture_camera(camera_index, device_name, output_dir):
    """
    打开指定摄像头，获取信息并拍照。
    """
    result = {
        "camera_index": camera_index,
        "device_name": device_name,
        "available": False,
        "open_success": False,
        "read_success": False,
        "image_path": None,
        "properties": {},
        "error": None,
        "backend_name": None,
        "frame_shape": None,
        "actual_width": None,
        "actual_height": None,
    }

    cap = None

    try:
        cap = open_camera(camera_index)

        if not cap.isOpened():
            result["error"] = "摄像头无法打开"
            return result

        result["open_success"] = True
        result["available"] = True

        try:
            result["backend_name"] = cap.getBackendName()
        except Exception:
            result["backend_name"] = "未知"

        time.sleep(WARMUP_TIME)

        frame = None
        ret = False

        for _ in range(DISCARD_FRAMES):
            ret, frame = cap.read()
            time.sleep(0.03)

        ret, frame = cap.read()

        result["properties"] = get_camera_properties(cap)

        if not ret or frame is None:
            result["error"] = "摄像头打开成功，但读取画面失败"
            return result

        result["read_success"] = True
        result["frame_shape"] = frame.shape

        height, width = frame.shape[:2]
        result["actual_width"] = width
        result["actual_height"] = height

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        safe_name = safe_filename(device_name)
        image_filename = (
            f"camera_index_{camera_index:02d}"
            f"__{safe_name}"
            f"__{width}x{height}"
            f"__{timestamp}.jpg"
        )

        image_path = os.path.join(output_dir, image_filename)

        ok = cv2.imwrite(image_path, frame)

        if ok:
            result["image_path"] = image_path
        else:
            result["error"] = "拍照成功，但保存图片失败"

    except Exception as e:
        result["error"] = str(e)

    finally:
        if cap is not None:
            cap.release()

    return result


def generate_report(results, report_path, device_info):
    """
    生成 TXT 报告。
    """
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("摄像头检测、设备信息与拍照报告\n")
        f.write("=" * 80 + "\n")
        f.write(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"OpenCV 版本：{cv2.__version__}\n")
        f.write(f"系统平台：{platform.platform()}\n")
        f.write(f"Python 版本：{sys.version}\n")
        f.write(f"扫描通道范围：0 ~ {MAX_CAMERA_INDEX}\n")
        f.write(f"设备名称获取来源：{device_info['source']}\n")
        f.write("=" * 80 + "\n\n")

        f.write("系统枚举到的摄像头设备名称\n")
        f.write("-" * 80 + "\n")

        if device_info["names"]:
            for i, name in enumerate(device_info["names"]):
                f.write(f"设备顺序 {i}：{name}\n")
        else:
            f.write("未枚举到摄像头设备名称\n")

        f.write("\n")

        available_count = sum(1 for r in results if r["available"])
        read_success_count = sum(1 for r in results if r["read_success"])

        f.write("汇总信息\n")
        f.write("-" * 80 + "\n")
        f.write(f"检测到可打开摄像头通道数量：{available_count}\n")
        f.write(f"成功拍照摄像头通道数量：{read_success_count}\n")
        f.write("\n")

        for result in results:
            f.write("=" * 80 + "\n")
            f.write(f"摄像头通道索引：{result['camera_index']}\n")
            f.write("-" * 80 + "\n")

            f.write(f"推测设备名称：{result['device_name']}\n")
            f.write(f"是否可打开：{result['open_success']}\n")
            f.write(f"是否成功读取画面：{result['read_success']}\n")
            f.write(f"OpenCV 后端：{result['backend_name']}\n")

            if result["frame_shape"] is not None:
                f.write(f"实际读取图像 shape：{result['frame_shape']}\n")

            if result["actual_width"] and result["actual_height"]:
                f.write(f"实际拍照分辨率：{result['actual_width']} x {result['actual_height']}\n")

            if result["image_path"]:
                f.write(f"照片保存路径：{result['image_path']}\n")
            else:
                f.write("照片保存路径：无\n")

            if result["error"]:
                f.write(f"错误信息：{result['error']}\n")

            f.write("\n摄像头属性信息：\n")

            if result["properties"]:
                for key, value in result["properties"].items():
                    if "FOURCC" in key and value is not None:
                        f.write(f"{key}：{value}，字符串：{fourcc_to_string(value)}\n")
                    else:
                        f.write(f"{key}：{value}\n")
            else:
                f.write("无可用属性信息\n")

            f.write("\n")

        f.write("=" * 80 + "\n")
        f.write("报告结束\n")


def main():
    app_dir = get_app_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 照片和报告单独放一个目录，避免和 exe 混在一起太乱
    output_dir = os.path.join(app_dir, f"camera_output_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)

    report_path = os.path.join(output_dir, f"camera_report_{timestamp}.txt")

    device_info = get_camera_device_names()
    device_names = device_info["names"]

    results = []

    for camera_index in range(MAX_CAMERA_INDEX + 1):
        if camera_index < len(device_names):
            device_name = device_names[camera_index]
        else:
            device_name = f"UnknownDevice_Index_{camera_index}"

        result = capture_camera(camera_index, device_name, output_dir)
        results.append(result)

    generate_report(results, report_path, device_info)


if __name__ == "__main__":
    main()
