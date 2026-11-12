"""Monotone reconciliation for callbacks and synchronous transport returns."""
from .domain import BusinessError, require


def merge_stage(current, observed, strict=False):
    require(observed in {'sent','delivered','unknown','failed','accepted'},'INVALID_RECEIPT','回执阶段无效')
    if current in {'sent','delivered'}:
        if observed=='failed' and strict:
            raise BusinessError('RECEIPT_CONFLICT','已确认发送后的失败回执需要独立核实')
        return 'delivered' if 'delivered' in (current,observed) else current
    if current=='accepted' and observed=='unknown':
        return current
    if current=='failed' and observed in {'unknown','accepted'}:
        return current
    return observed
