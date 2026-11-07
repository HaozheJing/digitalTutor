---
name: review-context
description: 执行已经安排的家庭资料内部复查，记录结果，不向家长发送消息。
---

读取任务的candidate_id及目的。调用review_context(candidate_id)核对当前课程、记忆和上下文，将返回的复查结果作为完成依据。此动作不要求生成对客消息，因此无需加载reply-style或调用发送工具。候选不存在时记录失败，不能编造已完成回执。
