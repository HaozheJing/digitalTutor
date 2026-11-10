"""Keep contact rules effective while limiting their quoted evidence to this conversation."""
import json


def for_conversation(connection, state, records):
    if not (state or {}).get('conversation_id'):
        return records
    def archived(source):
        return bool(source and connection.execute(
            'SELECT 1 FROM messages WHERE source_id=? AND visible=0', (source,)).fetchone())
    projected = []
    for record in records:
        row = dict(record)
        if archived(row.get('source_message_id')):
            row.pop('quote',None)
        if row.get('source'):
            sources = json.loads(row['source'])
            row['source'] = json.dumps([ref for ref in sources if not archived(ref.get('source_message_id'))],
                                       ensure_ascii=False)
        projected.append(row)
    return projected
