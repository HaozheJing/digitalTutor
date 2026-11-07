---
name: generate-reading-audio
description: 明确需要合成阅读口信音频时使用；只生成素材，不代表已发送。
---

核实当前请求确实需要音频后，用 generate_audio(content,request_id)生成；同一内容沿用稳定 request_id，不因未知结果更换ID重试。只有实际生成回执才能说素材已生成。当前统一交付尚不支持语音附件，不得将音频地址当作图片发送，也不得承诺已发送语音。配置缺失或失败时说明缺口，不编造素材。
