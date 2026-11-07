"""Validate the author's WeChat bubbles before creating any delivery."""
LIMIT = 50


def parts(body):
    if not isinstance(body, str) or not body.strip():
        raise ValueError('需要非空文字正文')
    if any(mark in body for mark in ('&#10;', '&#xA;', '&#13;', '\\n')):
        raise ValueError('正文里出现了换行编码。请重写为一句50字内的完整回答；需要多条时用实际换行，不要写HTML实体或反斜杠文字。一次提交整组。')
    bubbles = [line.strip() for line in body.splitlines() if line.strip()]
    for position, bubble in enumerate(bubbles, 1):
        if len(bubble) > LIMIT:
            raise ValueError(
                f'第{position}条有{len(bubble)}字，每条最多{LIMIT}字。'
                '请重新组织整组正文，每行一句完整的话。删掉非必要解释，再一次提交全部气泡。'
            )
        if bubble.endswith(('，', ',', '、', '：', ':')) or all(c in '…。. ' for c in bubble):
            raise ValueError(f'第{position}条没有说完。请改成能单独读懂的完整气泡，一次提交整组。')
    return bubbles
