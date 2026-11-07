"""Durable per-family/run emoji quantity and consecutive-delivery limits."""
import json
import sqlite3
from contextlib import closing
import sql_tools as s
import message_hooks as hooks


class PolicyViolation(ValueError):
    """A safe, actionable policy reason that may be returned to the model."""


def tables(c):
    c.execute('CREATE TABLE IF NOT EXISTS emoji_turns(seq INTEGER PRIMARY KEY,scope TEXT,action_key TEXT UNIQUE,owner TEXT,has_emoji INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS emoji_search_hits(scope TEXT,token TEXT,asset_id TEXT,version TEXT,document_sha256 TEXT,query TEXT,PRIMARY KEY(scope,token,asset_id,version))')


def owner(c, action_key=None):
    hooks.tables(c)
    token = hooks.AUTHORITY.get()
    if action_key:
        row=c.execute('SELECT token FROM message_hook_deliveries WHERE action_key=?',(action_key,)).fetchone()
        token=row[0] if row else token
    row=c.execute('SELECT owner_type,owner_id FROM message_hook_generations WHERE token=?',(token,)).fetchone()
    return token, ':'.join(row) if row else ''


def significant(c, row):
    _,scope,key,_,has_emoji=row
    delivery=c.execute('SELECT status FROM deliveries WHERE action_key=?',(key,)).fetchone()
    if not delivery:return False
    # Stable external IDs also cover a crash after the unknown reservation but
    # before test_outbox_parts was linked. Unknown must not permit another image.
    import hashlib
    ident='dsh-'+hashlib.sha256((scope+':'+key+':emoji:0').encode()).hexdigest()[:40]
    part=c.execute('SELECT status FROM test_outbox WHERE id=?',(ident,)).fetchone()
    if has_emoji:return delivery[0]=='queued' or bool(part and part[0] in ('unknown','accepted','sent'))
    return delivery[0]=='sent'


def check(c, action_key=None, owner_key=None):
    tables(c);scope=hooks.scope(c)
    current=c.execute('SELECT seq FROM emoji_turns WHERE action_key=?',(action_key,)).fetchone() if action_key else None
    rows=c.execute('SELECT seq,scope,action_key,owner,has_emoji FROM emoji_turns WHERE scope=? AND action_key<>? ORDER BY seq DESC',(scope,action_key or '')).fetchall()
    rows=[r for r in rows if (not current or r[0]<current[0]) and significant(c,r)]
    if owner_key and any(r[3]==owner_key and r[4] for r in rows):
        raise PolicyViolation('emoji_same_turn_limit: 本轮已有表情交付，请勿拆成另一条发送')
    previous=bool(rows[0][4]) if rows else None
    if previous is None:
        path=s.R/'config/emoji-history.json'
        history=json.loads(path.read_text()) if path.exists() else []
        previous=bool(history[-1]['has_emoji']) if history else False
    if previous:raise PolicyViolation('emoji_frequency_limit: 上一轮已选表情或送达未明，本轮请仅回复文字')


def register(c, action_key, refs, registry):
    tables(c);scope=hooks.scope(c);token,key=owner(c,action_key)
    if refs:
        if len(refs)>1:raise PolicyViolation('emoji_quantity_limit: 一次交付最多一张表情')
        check(c,action_key,key)
        if registry.get('retrieval'):
            for ref in refs:
                asset=next(a for a in registry['assets'] if a['asset_id']==ref['asset_id'])
                found=c.execute('SELECT document_sha256 FROM emoji_search_hits WHERE scope=? AND token=? AND asset_id=? AND version=?',
                                (scope,token,ref['asset_id'],ref['version'])).fetchone()
                if not found or found[0]!=asset['knowledge']['sha256']:
                    raise PolicyViolation('emoji_search_required: 请先以本轮语境检索OpenViking，再从返回素材中选择')
    c.execute('INSERT OR IGNORE INTO emoji_turns(scope,action_key,owner,has_emoji) VALUES(?,?,?,?)',(scope,action_key,key,bool(refs)))


def availability():
    with closing(sqlite3.connect(s.DB)) as c,c:
        try:check(c);return {'allowed':True,'max_per_delivery':1}
        except ValueError as exc:return {'allowed':False,'max_per_delivery':1,'reason':str(exc)}


def grant(cards,query):
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');tables(c);hooks.tables(c)
        row=c.execute("SELECT token FROM message_hook_generations WHERE scope=? AND cursor=? AND status='active' ORDER BY created_at DESC,rowid DESC LIMIT 1",(hooks.scope(c),hooks.cursor(c))).fetchone()
        if not row:return False
        hooks.validate(c,row[0])
        for a in cards:c.execute('INSERT OR REPLACE INTO emoji_search_hits VALUES(?,?,?,?,?,?)',
            (hooks.scope(c),row[0],a['asset_id'],a['version'],a['document_sha256'],query))
    return True
