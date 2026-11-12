"""Read only Hermes spillover files in this capability's isolated profile.

Native persistence advertises read_file. Provide that name with a deliberately
narrow implementation: no arbitrary filesystem access, bounded character pages.
"""
import hashlib
import json
import re
from pathlib import Path
from .domain import require, BusinessError


class ToolResultReader:
    def __init__(self, profile, business):
        self.root = (Path(profile) / 'cache' / 'spillover').resolve()
        self.business = business

    def source(self, path):
        self.business.app.auth(self.business.token, {'service','planner_daily','planner_progress','memory_daily'})
        requested = Path(path)
        require(requested.is_absolute() and not requested.is_symlink(), 'RESULT_SCOPE', '只能读取当前会话的工具溢出结果')
        resolved = requested.resolve()
        require(resolved.parent == self.root and resolved.is_file(), 'RESULT_SCOPE', '结果不属于当前会话或已不可用')
        return resolved.read_text(encoding='utf-8')

    def read(self, args):
        require(set(args) <= {'path','offset','limit','query'}, 'INVALID_SCHEMA', '不支持的读取参数')
        value = self.source(args.get('path',''))
        offset, limit = args.get('offset',0), args.get('limit',12000)
        require(type(offset) is int and 0 <= offset <= len(value) and type(limit) is int and 1 <= limit <= 12000,
                'INVALID_SCHEMA', 'offset为从0开始的字符位置，limit为1到12000个字符')
        query = args.get('query')
        found = None
        if query is not None:
            require(isinstance(query,str) and 0 < len(query) <= 200, 'INVALID_SCHEMA', 'query须为1到200字符的原文片段')
            found = value.find(query, offset)
            if found < 0:
                return {'ok':True,'path':args['path'],'query_found':False,'searched_from':offset,
                        'total_chars':len(value),'sha256':hashlib.sha256(value.encode()).hexdigest()}
            offset = max(offset, found - min(1000, limit // 4))
        end = min(len(value),offset+limit)
        return {'ok':True,'path':args['path'],'sha256':hashlib.sha256(value.encode()).hexdigest(),
                'offset':offset,'end':end,'total_chars':len(value),'next_offset':end if end<len(value) else None,
                'query_match_offset':found,'content':value[offset:end],
                'coverage':'只读取本页；未读取部分不能视为已知。offset/limit单位为字符。'}

    def audit(self, messages):
        checks=[]
        for message in messages:
            content=message.get('content')
            if message.get('role')!='tool' or not isinstance(content,str):continue
            item={'tool_call_id':message.get('tool_call_id'),'chars':len(content),
                  'sha256':hashlib.sha256(content.encode()).hexdigest(),'persisted':False}
            if content.startswith('<persisted-output>'):
                match=re.search(r'Full output saved to: ([^\r\n]+)',content)
                require(match is not None,'RESULT_UNRECOVERABLE','原生结果缺少补读地址')
                value=self.source(match.group(1).strip())
                item.update(persisted=True,recoverable=True,full_chars=len(value),
                            full_sha256=hashlib.sha256(value.encode()).hexdigest())
            require(not re.search(r'\n\n\[Truncated: tool response was [\d,]+ chars\. Full output could not be saved to sandbox\.\]$',content),
                    'RESULT_UNRECOVERABLE','原生结果截断且未保存，停止请求以避免无声遗漏')
            checks.append(item)
        return checks

    def register(self, trace, on_tool=None):
        from tools.registry import registry
        def invoke(args, **kwargs):
            try:result=self.read(args)
            except BusinessError as exc:result={'ok':False,'error':{'code':exc.code,'message':exc.message}}
            record={'tool':'read_file','ok':result['ok'],'error':result.get('error',{}).get('code'),
                    'at':self.business.app.clock.iso(),'offset':result.get('offset'),
                    'end':result.get('end'),'total_chars':result.get('total_chars'),
                    'source_sha256':result.get('sha256'),'query_found':result.get('query_found'),
                    'model_result_chars':len(json.dumps(result,ensure_ascii=False))}
            trace.append(record)
            if on_tool:on_tool(record)
            return json.dumps(result,ensure_ascii=False,separators=(',',':'))
        registry.register(name='read_file',toolset='digital_tutor',check_fn=lambda:True,handler=invoke,override=True,
            schema={'name':'read_file','description':'只读当前家庭会话的原生工具溢出结果。只能使用工具返回的Full output saved to路径，不能读取其他文件。offset/limit是字符数（不是行数）。用query定位原文片段，可继续next_offset分页。取得一页不代表已读完整文件。',
            'parameters':{'type':'object','properties':{'path':{'type':'string'},'offset':{'type':'integer','minimum':0},
                'limit':{'type':'integer','minimum':1,'maximum':12000},'query':{'type':'string','maxLength':200}},'required':['path'],'additionalProperties':False}})
        return 'read_file'
