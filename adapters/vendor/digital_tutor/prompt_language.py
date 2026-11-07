"""Localize framework-owned system prose on the wire, preserving native caches.

The native runtime keeps its own canonical prompt (including cwd validation
anchors). Only an exact copy of that prompt in a primary API request is adapted.
Business text, histories, tool schemas and steering delimiters are not rewritten.
"""
from .domain import require


class ChineseSystemPrompt:
    def __init__(self,full_system=None):
        self.full_system=full_system
        self.agent=None
        self._cache={}

    def localize(self,payload):
        import agent.system_prompt as native
        from agent import prompt_builder as builder
        agent=self.agent
        require(agent is not None,'PROMPT_LANGUAGE_SCOPE','中文提示词尚未绑定原生会话')
        cached=getattr(agent,'_cached_system_prompt',None)
        stable=getattr(agent,'_cached_system_prompt_static',None)
        require(isinstance(cached,str) and isinstance(stable,str) and stable and cached.startswith(stable),
                'PROMPT_LANGUAGE_LAYOUT','原生提示词结构变化，停止发送以免误改业务正文')
        if cached not in self._cache:
            owned=[native.HERMES_AGENT_HELP_GUIDANCE_NO_SKILLS,native.HERMES_AGENT_HELP_GUIDANCE,
                   native.TASK_COMPLETION_GUIDANCE,native.PARALLEL_TOOL_CALL_GUIDANCE,
                   native.STEER_CHANNEL_NOTE,native._active_profile_line(agent)]
            translated=localize_cached(cached,stable,owned,builder.RUNTIME_ENVIRONMENT_HEADING,builder.RUNTIME_ENVIRONMENT_END)
            if self.full_system is not None:
                require(self.full_system in cached,'PROMPT_LANGUAGE_LAYOUT','原生提示词未包含已确认全文')
                translated=self.full_system
            self._cache={cached:translated}
        changed=0
        for message in payload.get('messages',[]):
            content=message.get('content')
            if message.get('role')=='system' and isinstance(content,str) and (content==cached or content.startswith(cached+'\n')):
                message['content']=self._cache[cached]+content[len(cached):]
                changed+=1
        require(changed==1,'PROMPT_LANGUAGE_LAYOUT','实际请求未匹配唯一原生系统提示词')
        return {'language':'zh-CN','adapted_system_messages':changed,'native_cache_preserved':True}


def localize_cached(cached,stable,owned,environment_heading,environment_end):
    """Operate only on renderer-owned prefix/tail, not matching business prose."""
    require(cached.startswith(stable),'PROMPT_LANGUAGE_LAYOUT','原生稳定前缀不匹配')
    prefix=stable
    for block in owned:
        if block:prefix=prefix.replace(block,'',1)
    prefix='\n\n'.join(p.strip() for p in prefix.split('\n\n') if p.strip())
    tail=cached[len(stable):]
    # Native renderer guarantees its environment anchor follows all project and
    # plugin prose. Keep it in native cache; remove only this final wire block.
    start=tail.rfind('\n\n'+environment_heading+'\n\n')
    require(start>=0 and tail.rstrip().endswith(environment_end),'PROMPT_LANGUAGE_LAYOUT','未识别原生环境区块')
    before_environment=tail[:start]
    before_time,separator,timestamp=before_environment.rpartition('\n\n')
    require(separator and timestamp.startswith(('Conversation started:','Timezone:')),
            'PROMPT_LANGUAGE_LAYOUT','未识别原生时间元数据区块')
    return prefix+before_time
