"""Small deterministic query expansion. Matches are hints, never evidence."""
import re

SYNONYMS=(('读书','阅读','看书'),('提醒','通知','催促'),('晚上','晚间','夜间'),
          ('习惯','规律'),('偏好','喜欢'),('复习','回顾'),('作业','练习'))


def matches(query,text):
    if not query:return True
    terms=re.findall(r'[^\s,，;；]+',query.casefold())
    text=text.casefold()
    for term in terms:
        alternatives={term}
        for group in SYNONYMS:
            for word in group:
                if word in term:alternatives.update(term.replace(word,other) for other in group)
        if not any(word in text for word in alternatives):return False
    return True
