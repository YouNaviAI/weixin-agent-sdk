"""
微信 ilink 协议类型定义。

镜像 proto：GetUpdatesReq/Resp、WeixinMessage、SendMessageReq 等。
API 使用 JSON over HTTP，bytes 字段在 JSON 中以 base64 字符串表示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# 媒体类型枚举
# ---------------------------------------------------------------------------

class UploadMediaType:
    """上传媒体类型常量。"""
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class MessageType:
    """消息方向常量。"""
    NONE = 0
    USER = 1   # 用户发出
    BOT = 2    # 机器人发出


class MessageItemType:
    """消息条目类型常量。"""
    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageState:
    """消息状态常量。"""
    NEW = 0
    GENERATING = 1
    FINISH = 2


class TypingStatus:
    """打字状态常量。"""
    TYPING = 1
    CANCEL = 2


# ---------------------------------------------------------------------------
# 通用基础信息
# ---------------------------------------------------------------------------

@dataclass
class BaseInfo:
    """每条 CGI 请求都附带的公共元数据。"""
    channel_version: str | None = None


# ---------------------------------------------------------------------------
# 上传 URL 请求/响应
# ---------------------------------------------------------------------------

@dataclass
class GetUploadUrlReq:
    """获取 CDN 预签名上传 URL 的请求体。"""
    filekey: str | None = None
    media_type: int | None = None        # 参见 UploadMediaType
    to_user_id: str | None = None
    rawsize: int | None = None           # 原始明文大小（字节）
    rawfilemd5: str | None = None        # 原始明文 MD5
    filesize: int | None = None          # AES-128-ECB 加密后大小
    thumb_rawsize: int | None = None     # 缩略图明文大小（IMAGE/VIDEO 时必填）
    thumb_rawfilemd5: str | None = None  # 缩略图明文 MD5（IMAGE/VIDEO 时必填）
    thumb_filesize: int | None = None    # 缩略图密文大小（IMAGE/VIDEO 时必填）
    no_need_thumb: bool | None = None    # 不需要缩略图上传 URL
    aeskey: str | None = None           # AES 加密密钥（base64）

    @classmethod
    def from_dict(cls, data: Any) -> GetUploadUrlReq:
        if not isinstance(data, dict):
            return cls()
        return cls(
            filekey=data.get("filekey"),
            media_type=data.get("media_type"),
            to_user_id=data.get("to_user_id"),
            rawsize=data.get("rawsize"),
            rawfilemd5=data.get("rawfilemd5"),
            filesize=data.get("filesize"),
            thumb_rawsize=data.get("thumb_rawsize"),
            thumb_rawfilemd5=data.get("thumb_rawfilemd5"),
            thumb_filesize=data.get("thumb_filesize"),
            no_need_thumb=data.get("no_need_thumb"),
            aeskey=data.get("aeskey"),
        )


@dataclass
class GetUploadUrlResp:
    """CDN 上传 URL 响应。"""
    upload_param: str | None = None        # 旧格式：原图上传加密参数
    thumb_upload_param: str | None = None  # 旧格式：缩略图上传加密参数
    upload_full_url: str | None = None     # 新格式：完整上传 URL（优先使用）

    @classmethod
    def from_dict(cls, data: Any) -> GetUploadUrlResp:
        if not isinstance(data, dict):
            return cls()
        return cls(
            upload_param=data.get("upload_param"),
            thumb_upload_param=data.get("thumb_upload_param"),
            upload_full_url=data.get("upload_full_url"),
        )


# ---------------------------------------------------------------------------
# CDN 媒体引用
# ---------------------------------------------------------------------------

@dataclass
class CDNMedia:
    """CDN 媒体资源引用；aes_key 在 JSON 中为 base64 编码。"""
    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None  # 0=只加密 fileid，1=打包缩略图等信息

    @classmethod
    def from_dict(cls, data: Any) -> CDNMedia:
        if not isinstance(data, dict):
            return cls()
        return cls(
            encrypt_query_param=data.get("encrypt_query_param"),
            aes_key=data.get("aes_key"),
            encrypt_type=data.get("encrypt_type"),
        )


# ---------------------------------------------------------------------------
# 消息条目子类型
# ---------------------------------------------------------------------------

@dataclass
class TextItem:
    text: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> TextItem:
        if not isinstance(data, dict):
            return cls()
        return cls(text=data.get("text"))


@dataclass
class ImageItem:
    media: CDNMedia | None = None
    thumb_media: CDNMedia | None = None
    aeskey: str | None = None      # 原始 AES-128 密钥（hex，16 字节）；入站解密优先使用
    url: str | None = None
    mid_size: int | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None
    hd_size: int | None = None

    @classmethod
    def from_dict(cls, data: Any) -> ImageItem:
        if not isinstance(data, dict):
            return cls()
        return cls(
            media=CDNMedia.from_dict(data["media"]) if "media" in data else None,
            thumb_media=CDNMedia.from_dict(data["thumb_media"]) if "thumb_media" in data else None,
            aeskey=data.get("aeskey"),
            url=data.get("url"),
            mid_size=data.get("mid_size"),
            thumb_size=data.get("thumb_size"),
            thumb_height=data.get("thumb_height"),
            thumb_width=data.get("thumb_width"),
            hd_size=data.get("hd_size"),
        )


@dataclass
class VoiceItem:
    media: CDNMedia | None = None
    encode_type: int | None = None    # 1=pcm 2=adpcm 3=feature 4=speex 5=amr 6=silk 7=mp3 8=ogg-speex
    bits_per_sample: int | None = None
    sample_rate: int | None = None    # 采样率（Hz）
    playtime: int | None = None       # 语音长度（毫秒）
    text: str | None = None           # 语音转文字内容

    @classmethod
    def from_dict(cls, data: Any) -> VoiceItem:
        if not isinstance(data, dict):
            return cls()
        return cls(
            media=CDNMedia.from_dict(data["media"]) if "media" in data else None,
            encode_type=data.get("encode_type"),
            bits_per_sample=data.get("bits_per_sample"),
            sample_rate=data.get("sample_rate"),
            playtime=data.get("playtime"),
            text=data.get("text"),
        )


@dataclass
class FileItem:
    media: CDNMedia | None = None
    file_name: str | None = None
    md5: str | None = None
    len: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> FileItem:
        if not isinstance(data, dict):
            return cls()
        return cls(
            media=CDNMedia.from_dict(data["media"]) if "media" in data else None,
            file_name=data.get("file_name"),
            md5=data.get("md5"),
            len=data.get("len"),
        )


@dataclass
class VideoItem:
    media: CDNMedia | None = None
    video_size: int | None = None
    play_length: int | None = None
    video_md5: str | None = None
    thumb_media: CDNMedia | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None

    @classmethod
    def from_dict(cls, data: Any) -> VideoItem:
        if not isinstance(data, dict):
            return cls()
        return cls(
            media=CDNMedia.from_dict(data["media"]) if "media" in data else None,
            video_size=data.get("video_size"),
            play_length=data.get("play_length"),
            video_md5=data.get("video_md5"),
            thumb_media=CDNMedia.from_dict(data["thumb_media"]) if "thumb_media" in data else None,
            thumb_size=data.get("thumb_size"),
            thumb_height=data.get("thumb_height"),
            thumb_width=data.get("thumb_width"),
        )


# ---------------------------------------------------------------------------
# 消息条目（MessageItem）与引用消息
# ---------------------------------------------------------------------------

@dataclass
class MessageItem:
    """单条消息内容项，类型由 type 字段区分。"""
    type: int | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    is_completed: bool | None = None
    msg_id: str | None = None
    ref_msg: RefMessage | None = None
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    voice_item: VoiceItem | None = None
    file_item: FileItem | None = None
    video_item: VideoItem | None = None

    @classmethod
    def from_dict(cls, data: Any) -> MessageItem:
        if not isinstance(data, dict):
            return cls()
        return cls(
            type=data.get("type"),
            create_time_ms=data.get("create_time_ms"),
            update_time_ms=data.get("update_time_ms"),
            is_completed=data.get("is_completed"),
            msg_id=data.get("msg_id"),
            ref_msg=RefMessage.from_dict(data["ref_msg"]) if "ref_msg" in data else None,
            text_item=TextItem.from_dict(data["text_item"]) if "text_item" in data else None,
            image_item=ImageItem.from_dict(data["image_item"]) if "image_item" in data else None,
            voice_item=VoiceItem.from_dict(data["voice_item"]) if "voice_item" in data else None,
            file_item=FileItem.from_dict(data["file_item"]) if "file_item" in data else None,
            video_item=VideoItem.from_dict(data["video_item"]) if "video_item" in data else None,
        )


@dataclass
class RefMessage:
    """引用消息（摘要 + 原始消息项）。"""
    message_item: MessageItem | None = None
    title: str | None = None  # 摘要文本

    @classmethod
    def from_dict(cls, data: Any) -> RefMessage:
        if not isinstance(data, dict):
            return cls()
        return cls(
            message_item=MessageItem.from_dict(data["message_item"]) if "message_item" in data else None,
            title=data.get("title"),
        )


# ---------------------------------------------------------------------------
# 统一消息结构（WeixinMessage）
# ---------------------------------------------------------------------------

@dataclass
class WeixinMessage:
    """统一微信消息（proto: WeixinMessage）。"""
    seq: int | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    to_user_id: str | None = None
    client_id: str | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    delete_time_ms: int | None = None
    session_id: str | None = None
    group_id: str | None = None
    message_type: int | None = None
    message_state: int | None = None
    item_list: list[MessageItem] = field(default_factory=list)
    context_token: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> WeixinMessage:
        if not isinstance(data, dict):
            return cls()
        raw_items = data.get("item_list") or []
        return cls(
            seq=data.get("seq"),
            message_id=data.get("message_id"),
            from_user_id=data.get("from_user_id"),
            to_user_id=data.get("to_user_id"),
            client_id=data.get("client_id"),
            create_time_ms=data.get("create_time_ms"),
            update_time_ms=data.get("update_time_ms"),
            delete_time_ms=data.get("delete_time_ms"),
            session_id=data.get("session_id"),
            group_id=data.get("group_id"),
            message_type=data.get("message_type"),
            message_state=data.get("message_state"),
            item_list=[MessageItem.from_dict(i) for i in raw_items if isinstance(i, dict)],
            context_token=data.get("context_token"),
        )


# ---------------------------------------------------------------------------
# GetUpdates 请求/响应
# ---------------------------------------------------------------------------

@dataclass
class GetUpdatesResp:
    """getUpdates 响应。"""
    ret: int | None = None
    errcode: int | None = None        # 错误码，-14 表示会话过期
    errmsg: str | None = None
    msgs: list[WeixinMessage] = field(default_factory=list)
    sync_buf: str | None = None       # 已废弃，仅兼容旧版
    get_updates_buf: str | None = None
    longpolling_timeout_ms: int | None = None  # 服务端建议的下次长轮询超时

    @classmethod
    def from_dict(cls, data: Any) -> GetUpdatesResp:
        if not isinstance(data, dict):
            return cls()
        raw_msgs = data.get("msgs") or []
        return cls(
            ret=data.get("ret"),
            errcode=data.get("errcode"),
            errmsg=data.get("errmsg"),
            msgs=[WeixinMessage.from_dict(m) for m in raw_msgs if isinstance(m, dict)],
            sync_buf=data.get("sync_buf"),
            get_updates_buf=data.get("get_updates_buf"),
            longpolling_timeout_ms=data.get("longpolling_timeout_ms"),
        )


# ---------------------------------------------------------------------------
# SendMessage 请求
# ---------------------------------------------------------------------------

@dataclass
class SendMessageReq:
    """sendMessage 请求，封装单条 WeixinMessage。"""
    msg: WeixinMessage | None = None


# ---------------------------------------------------------------------------
# SendTyping 请求/响应
# ---------------------------------------------------------------------------

@dataclass
class SendTypingReq:
    """发送打字状态指示器的请求。"""
    ilink_user_id: str | None = None
    typing_ticket: str | None = None
    status: int | None = None  # 1=正在输入，2=取消输入


@dataclass
class SendTypingResp:
    ret: int | None = None
    errmsg: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> SendTypingResp:
        if not isinstance(data, dict):
            return cls()
        return cls(ret=data.get("ret"), errmsg=data.get("errmsg"))


# ---------------------------------------------------------------------------
# GetConfig 响应
# ---------------------------------------------------------------------------

@dataclass
class GetConfigResp:
    """获取机器人配置响应（含 typing_ticket）。"""
    ret: int | None = None
    errmsg: str | None = None
    typing_ticket: str | None = None  # base64 编码的打字票据，用于 sendTyping

    @classmethod
    def from_dict(cls, data: Any) -> GetConfigResp:
        if not isinstance(data, dict):
            return cls()
        return cls(
            ret=data.get("ret"),
            errmsg=data.get("errmsg"),
            typing_ticket=data.get("typing_ticket"),
        )


# ---------------------------------------------------------------------------
# QR 登录响应
# ---------------------------------------------------------------------------

@dataclass
class BotQrcodeResp:
    """get_bot_qrcode 响应：服务端返回的二维码信息。"""
    qrcode: str                  # 二维码标识，用于轮询状态
    qrcode_img_content: str      # 可被微信扫描的 URL

    @classmethod
    def from_dict(cls, data: Any) -> BotQrcodeResp:
        if not isinstance(data, dict):
            return cls(qrcode="", qrcode_img_content="")
        return cls(
            qrcode=data.get("qrcode") or "",
            qrcode_img_content=data.get("qrcode_img_content") or "",
        )


@dataclass
class QrcodeStatusResp:
    """
    get_qrcode_status 响应：二维码当前扫码状态。

    注：'scaned' 为服务端原始拼写（非笔误），保持不变。
    """
    status: Literal["wait", "scaned", "confirmed", "expired"]
    bot_token: str | None = None
    ilink_bot_id: str | None = None
    ilink_user_id: str | None = None
    baseurl: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> QrcodeStatusResp:
        if not isinstance(data, dict):
            return cls(status="wait")
        return cls(
            status=data.get("status") or "wait",
            bot_token=data.get("bot_token"),
            ilink_bot_id=data.get("ilink_bot_id"),
            ilink_user_id=data.get("ilink_user_id"),
            baseurl=data.get("baseurl"),
        )


# ---------------------------------------------------------------------------
# 账号文件序列化结构
# ---------------------------------------------------------------------------

@dataclass
class WeixinAccountFile:
    """
    accounts/<id>.json 的完整序列化结构。

    字段名采用 Python 惯例，to_dict() 输出驼峰 JSON 键以匹配文件格式。
    """
    token: str | None = None
    saved_at: str | None = None
    base_url: str | None = None
    user_id: str | None = None

    def to_dict(self) -> Any:
        """序列化为 JSON 可写对象，仅包含非空字段，键名使用驼峰命名。"""
        result = {}
        if self.token:
            result["token"] = self.token
        if self.saved_at:
            result["savedAt"] = self.saved_at
        if self.base_url:
            result["baseUrl"] = self.base_url
        if self.user_id:
            result["userId"] = self.user_id
        return result
