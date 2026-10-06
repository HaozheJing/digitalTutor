"""Check directly visible SQL filter values; not a SQL parser or security boundary.

SQLite still validates SQL and enforces the existing read-only UID projection.
Indirect values supplied by SELECT/CTE/aliases deliberately remain supported.
"""
from dataclasses import dataclass
import re


class BindingPolicyError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


_TOKEN = re.compile(r'''(?P<skip>\s+|--[^\r\n]*|/\*.*?(?:\*/|$))
 |(?P<string>'(?:''|[^'])*')
 |(?P<identifier>"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]*\])
 |(?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
 |(?P<parameter>[:@$][A-Za-z_][A-Za-z_0-9]*)
 |(?P<word>[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*)
 |(?P<symbol><>|!=|==|<=|>=|\|\||.)''', re.X | re.S)
_COMPARE = {'=', '==', '!=', '<>', '<', '>', '<=', '>=',
            'LIKE', 'GLOB', 'MATCH', 'REGEXP', 'IN', 'BETWEEN', 'IS'}
_START = {'WHERE', 'HAVING', 'ON', 'WHEN'}
_STOP = {'SELECT', 'FROM', 'GROUP', 'ORDER', 'LIMIT', 'OFFSET', 'UNION',
         'INTERSECT', 'EXCEPT', 'WINDOW', 'JOIN', 'RETURNING'}
_KEYWORDS = _START | _STOP | _COMPARE | {
    'AND', 'OR', 'NOT', 'NULL', 'TRUE', 'FALSE', 'AS', 'DISTINCT', 'CASE',
    'THEN', 'ELSE', 'END', 'COLLATE', 'ASC', 'DESC', 'BY', 'WITH', 'EXISTS',
    'ALL', 'FILTER', 'OVER', 'USING', 'ESCAPE'}
MAX_GROUP_DEPTH = 64


@dataclass(eq=False)
class Token:
    kind: str
    text: str
    offset: int

    @property
    def keyword(self):
        return self.text.upper() if self.kind in {'word', 'symbol'} else ''


@dataclass(eq=False)
class Group:
    items: list
    function: str = ''


def _function_name(token):
    if not isinstance(token, Token): return ''
    if token.kind == 'word':
        return token.text.lower() if token.keyword not in _KEYWORDS else ''
    if token.kind == 'identifier':
        text = token.text
        return text[1:-1].replace(text[0] * 2, text[0]).lower()
    return ''


def _group(tokens, start=0, depth=0):
    items = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token.text == ')' and token.kind == 'symbol':
            return items, index + 1
        if token.text == '(' and token.kind == 'symbol':
            if depth >= MAX_GROUP_DEPTH:
                raise BindingPolicyError('SQL_FILTER_NESTING_LIMIT', 'SQL括号嵌套超过可诊断检查深度，请简化查询')
            previous = items[-1] if items else None
            function = _function_name(previous)
            children, index = _group(tokens, index + 1, depth + 1)
            items.append(Group(children, function))
        else:
            items.append(token)
            index += 1
    return items, index


def _leaves(items):
    for item in items:
        if isinstance(item, Group):
            yield from _leaves(item.items)
        else:
            yield item


def _arguments(items):
    result = [[]]
    for item in items:
        if isinstance(item, Token) and item.text == ',' and item.kind == 'symbol':
            result.append([])
        else:
            result[-1].append(item)
    return result


def _dynamic(items):
    for i, item in enumerate(items):
        if isinstance(item, Group):
            if _dynamic(item.items): return True
        elif i + 1 < len(items) and isinstance(items[i + 1], Group) and items[i + 1].function:
            continue
        elif item.kind in {'parameter', 'identifier'}:
            return True
        elif item.kind == 'word' and item.keyword not in _KEYWORDS:
            if i + 1 == len(items) or not isinstance(items[i + 1], Group) or not items[i + 1].function:
                return True
    return False


def _literal(token):
    return token.kind in {'number', 'string'} or token.keyword in {'NULL', 'TRUE', 'FALSE'}


def _fixed_function_literals(group):
    """Finite structural argument positions on dynamic input, not entire calls."""
    args = _arguments(group.items)
    dynamic_index = 1 if group.function == 'strftime' else 0
    if len(args) <= dynamic_index or not _dynamic(args[dynamic_index]): return set()
    ignored = set()
    for index, arg in enumerate(args):
        if index == dynamic_index: continue
        leaves = list(_leaves(arg))
        if group.function in {'json_extract', 'json_array_length', 'date', 'datetime', 'strftime'}:
            valid = len(leaves) == 1 and leaves[0].kind == 'string'
        elif group.function == 'trim' and index == 1:
            valid = len(leaves) == 1 and leaves[0].kind == 'string'
        elif ((group.function in {'substr', 'substring'} and index in {1, 2})
              or (group.function == 'round' and index == 1)):
            valid = bool(leaves) and all(t.kind == 'number' or t.keyword in {'+', '-'} for t in leaves)
        elif group.function in {'coalesce', 'ifnull', 'nullif'}:
            valid = bool(leaves) and all(_literal(t) or t.keyword in {'+', '-'} for t in leaves)
        else:
            valid = False
        if valid: ignored.update(t for t in leaves if _literal(t))
    return ignored


def check_filter_bindings(sql, parameters):
    """Return names actually used in directly visible predicate expressions.

    Does not assert that bound values are semantically correct or rows exist.
    Quoted identifiers are identifiers, not string literals (SQLite DQS caveat).
    """
    tokens = [Token(m.lastgroup, m.group(), m.start()) for m in _TOKEN.finditer(sql) if m.lastgroup != 'skip']
    groups, _ = _group(tokens)
    used = set()

    def scan(items, inherited=False, ignored=frozenset()):
        # A subquery has its own SELECT/projection/WHERE scope. Do not follow data flow.
        predicate = inherited
        case_scopes = []
        ignored = set(ignored)
        for i, item in enumerate(items):
            if isinstance(item, Group):
                ignored.update(_fixed_function_literals(item))
            # NULL is structural only for IS [NOT] NULL, including (NULL).
            previous = [x.keyword for x in items[max(0, i-2):i] if isinstance(x, Token)]
            if previous[-1:] == ['IS'] or previous[-2:] == ['IS', 'NOT']:
                leaves = list(_leaves(item.items)) if isinstance(item, Group) else [item]
                if len(leaves) == 1 and leaves[0].keyword == 'NULL': ignored.add(leaves[0])

        # Comparisons can also occur in SELECT/CASE expressions, without WHERE.
        compared = set()
        segment = []
        def finish():
            if any(isinstance(t, Token) and t.keyword in _COMPARE for t in segment):
                compared.update(segment)
            segment.clear()
        for item in items:
            key = item.keyword if isinstance(item, Token) else ''
            if key in _START | _STOP | {'THEN', 'ELSE', 'END', ',', ';'}:
                finish()
            else:
                segment.append(item)
        finish()

        for i, item in enumerate(items):
            if isinstance(item, Group):
                scan(item.items, predicate or item in compared, ignored)
                continue
            key = item.keyword
            if key == 'CASE': case_scopes.append(predicate)
            elif key in {'THEN', 'ELSE'}: predicate = case_scopes[-1] if case_scopes else inherited
            elif key == 'END': predicate = case_scopes.pop() if case_scopes else inherited
            elif key in _START: predicate = True
            elif key in _STOP: predicate = False
            active = predicate or item in compared
            if active and item not in ignored and _literal(item):
                raise BindingPolicyError('SQL_FILTER_REQUIRES_PARAMETER',
                    '筛选值请使用命名参数及parameters；条件中存在值字面量（位置%d）' % item.offset)
            if active and item.kind == 'parameter':
                name = item.text[1:]
                if name not in parameters:
                    raise BindingPolicyError('SQL_FILTER_PARAMETER_MISSING', '条件中的命名参数未提供绑定值')
                used.add(name)
            if active and key == '?':
                raise BindingPolicyError('SQL_FILTER_REQUIRES_PARAMETER', '筛选值请使用命名参数，不能使用位置参数')

    scan(groups)
    return {'direct_predicate_parameters': sorted(used), 'scope': 'direct_literal_predicates_only'}
