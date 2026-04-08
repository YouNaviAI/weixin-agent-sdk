"""
SILK 音频格式转码为 WAV。

微信语音消息使用 SILK 格式（encode_type=6）。
本模块尝试通过 pysilk 库解码为 PCM，再封装成 WAV 容器。

pysilk 未安装时返回 None，调用方应回退到直接保存原始 SILK 文件。
"""

from __future__ import annotations

import asyncio
import struct

from weixin_agent_sdk.util.logger import logger

SILK_SAMPLE_RATE = 24_000  # 微信语音消息默认采样率（Hz）


def pcm_bytes_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """
    将原始 PCM（16 位有符号小端，单声道）数据封装进 WAV 容器。
    返回完整 WAV 文件字节串。
    """
    pcm_size = len(pcm)
    # WAV 文件头：RIFF(4) + 文件大小(4) + WAVE(4) + fmt (4) + fmt块大小(4)
    # + 音频格式(2) + 声道数(2) + 采样率(4) + 字节率(4) + 块对齐(2) + 位深(2)
    # + data(4) + 数据大小(4) = 44 字节头部
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + pcm_size,      # 总文件大小 - 8
        b"WAVE",
        b"fmt ",
        16,                 # fmt 块大小
        1,                  # PCM 格式
        1,                  # 单声道
        sample_rate,
        sample_rate * 2,    # 字节率（单声道 16 位 = 采样率 × 2）
        2,                  # 块对齐（单声道 16 位 = 2 字节/帧）
        16,                 # 位深
        b"data",
        pcm_size,
    )
    return header + pcm


async def silk_to_wav(silk_buf: bytes) -> bytes | None:
    """
    将 SILK 音频缓冲区转码为 WAV 字节串。

    依赖 pysilk 库（可选依赖）。未安装或解码失败时返回 None，
    调用方应回退到直接保存原始 SILK 文件。
    pysilk.decode 为 CPU 密集型阻塞操作，在线程池中执行。
    """
    try:
        import pysilk  # type: ignore[import]
    except ImportError:
        logger.debug("silk_to_wav: pysilk 未安装，跳过 SILK 转码")
        return None

    try:
        logger.debug(f"silk_to_wav: 解码 {len(silk_buf)} 字节 SILK")
        pcm = await asyncio.to_thread(pysilk.decode, silk_buf, SILK_SAMPLE_RATE)
        logger.debug(f"silk_to_wav: 解码完成，PCM {len(pcm)} 字节")
        wav = await asyncio.to_thread(pcm_bytes_to_wav, pcm, SILK_SAMPLE_RATE)
        return wav
    except Exception as exc:
        logger.warn(f"silk_to_wav: SILK 解码失败，将回退到原始文件: {exc}")
        return None
