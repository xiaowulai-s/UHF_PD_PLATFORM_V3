# -*- coding: utf-8 -*-
"""Serial-port utility helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from serial.tools import list_ports

    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    list_ports = None


@dataclass
class SerialPortInfo:
    """Structured serial-port metadata."""

    device: str
    description: str
    hwid: str
    vid: Optional[str]
    pid: Optional[str]
    serial_number: Optional[str]
    location: Optional[str]
    manufacturer: Optional[str]
    product: Optional[str]
    interface: Optional[str]


def list_serial_ports() -> List[str]:
    """Return available serial port device names."""
    if not SERIAL_AVAILABLE:
        return []
    return [port.device for port in list_ports.comports()]


def get_serial_ports_info() -> List[SerialPortInfo]:
    """Return structured information for all available serial ports."""
    if not SERIAL_AVAILABLE:
        return []

    return [
        SerialPortInfo(
            device=port.device,
            description=port.description,
            hwid=port.hwid,
            vid=port.vid,
            pid=port.pid,
            serial_number=port.serial_number,
            location=port.location,
            manufacturer=port.manufacturer,
            product=port.product,
            interface=port.interface,
        )
        for port in list_ports.comports()
    ]


def get_serial_port_by_description(keyword: str) -> Optional[str]:
    """Return the first serial port whose description contains the keyword."""
    if not SERIAL_AVAILABLE:
        return None

    for port in list_ports.comports():
        if keyword.lower() in port.description.lower():
            return port.device
    return None


def get_serial_port_details(device: str) -> Optional[SerialPortInfo]:
    """Return detailed information for a specific serial port."""
    if not SERIAL_AVAILABLE:
        return None

    for port in list_ports.comports():
        if port.device == device:
            return SerialPortInfo(
                device=port.device,
                description=port.description,
                hwid=port.hwid,
                vid=port.vid,
                pid=port.pid,
                serial_number=port.serial_number,
                location=port.location,
                manufacturer=port.manufacturer,
                product=port.product,
                interface=port.interface,
            )
    return None


def serial_port_to_dict(port: Any) -> Dict[str, Any]:
    """Convert a serial port object into a plain dictionary."""
    if not SERIAL_AVAILABLE:
        return {}

    return {
        "device": port.device,
        "description": port.description,
        "hwid": port.hwid,
        "vid": port.vid,
        "pid": port.pid,
        "serial_number": port.serial_number,
        "location": port.location,
        "manufacturer": port.manufacturer,
        "product": port.product,
        "interface": port.interface,
    }


def list_serial_ports_as_dict() -> List[Dict[str, Any]]:
    """Return all available serial ports as dictionaries."""
    if not SERIAL_AVAILABLE:
        return []
    return [serial_port_to_dict(port) for port in list_ports.comports()]


def is_serial_available() -> bool:
    """Return whether pyserial is installed and usable."""
    return SERIAL_AVAILABLE


def get_port_count() -> int:
    """Return the number of available serial ports."""
    if not SERIAL_AVAILABLE:
        return 0
    return len(list_ports.comports())


def test_serial_port(
    device: str, baudrate: int = 9600, timeout: float = 1.0, test_data: bytes = b"AT\r\n"
) -> Tuple[bool, str]:
    """Try opening a serial port and optionally writing a probe payload."""
    if not SERIAL_AVAILABLE:
        return False, "pyserial 未安装"

    try:
        import serial

        connection = serial.Serial(port=device, baudrate=baudrate, timeout=timeout)
        try:
            connection.write(test_data)
            try:
                connection.read(100)
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("清空串口缓冲区异常: %s", e)
        finally:
            connection.close()

        return True, f"串口 {device} 测试成功"
    except serial.SerialException as exc:
        return False, f"串口错误：{exc}"
    except Exception as exc:
        return False, f"测试失败：{exc}"


test_serial_port.__test__ = False


def get_serial_port_status(device: str) -> Dict[str, Any]:
    """Return availability and metadata for one serial port."""
    if not SERIAL_AVAILABLE:
        return {"available": False, "error": "pyserial 未安装"}

    try:
        import serial

        port_info = get_serial_port_details(device)
        if port_info is None:
            return {"available": False, "error": f"串口 {device} 不存在"}

        try:
            connection = serial.Serial(port=device, baudrate=9600, timeout=0.1)
            connection.close()
            return {
                "available": True,
                "device": device,
                "description": port_info.description,
                "hwid": port_info.hwid,
                "error": None,
            }
        except serial.SerialException as exc:
            return {"available": False, "device": device, "error": f"无法打开串口：{exc}"}
    except Exception as exc:
        return {"available": False, "error": f"检查失败：{exc}"}
