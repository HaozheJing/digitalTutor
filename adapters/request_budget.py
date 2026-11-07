"""Complete-wire admission estimate calibrated against 256 historical requests.

This is not a tokenizer or a guarantee for arbitrary languages/media. The
observed worst actual/base ratio was 1.051159; use 1.25 plus output/headroom.
"""
import json
CONTEXT_WINDOW = 131072
MIN_OUTPUT = 1024
SAFETY = 4096

class BudgetExceeded(ValueError):
    def __init__(self, code, budget):
        super().__init__(code); self.code=code; self.budget=budget

def check(body, *, context_window=CONTEXT_WINDOW, default_output=8192, incoming=None):
    caps=[body[k] for k in ('max_tokens','max_completion_tokens') if k in body]
    raw=[(incoming or {})[k] for k in ('max_tokens','max_completion_tokens') if k in (incoming or {})]
    output=min(caps) if caps else default_output
    metadata={'context_window':context_window,'output_tokens':output,'minimum_output':MIN_OUTPUT,
              'safety_tokens':SAFETY,'estimator':'calibrated_text_v2','calibration_samples':256,'calibration_max_ratio':1.051159,'margin_factor':1.25}
    if any(type(x) is not int or x<MIN_OUTPUT for x in [output,*raw]):
        raise BudgetExceeded('output_budget_exhausted',metadata)
    images=0
    def textual(v):
        nonlocal images
        if isinstance(v,list):return [textual(x) for x in v]
        if not isinstance(v,dict):return v
        if v.get('type') in ('image_url','input_image'):
            images+=1;return {'type':v['type'],'image':'reserved'}
        return {k:textual(x) for k,x in v.items()}
    raw=json.dumps(textual(body),ensure_ascii=False,separators=(',',':'))
    size=len(raw.encode('utf8'))
    ascii_chars=sum(ord(c)<128 for c in raw)
    wide_chars=len(raw)-ascii_chars
    # Complete body includes system messages, tools, arguments, results and Skill text.
    estimate=__import__('math').ceil((ascii_chars/3+wide_chars+1024)*1.25)+images*16384
    metadata.update(text_utf8_bytes=size,image_count=images,input_upper_estimate=estimate)
    if estimate+output+SAFETY>context_window:
        raise BudgetExceeded('request_context_budget_exceeded',metadata)
    return metadata
