"""Independent pre-publication review, cached against source and current glossary."""
import hashlib
import json

from . import store
from .language import chat
from .media import Waiting
from .qwen import save


def review(task, settings, folder):
    source = json.loads((folder / 'source.json').read_text('utf-8'))
    metadata = json.loads((folder / 'posting.json').read_text('utf-8'))
    notes = {'global': settings.translation_notes,
             'channel': task['payload'].get('options', {}).get('translation_notes', '')}
    content = {'original_title': source['title'], 'description': (source.get('description') or '')[:5000],
               'translated_title': metadata['title'], 'current_glossary': notes}
    def signature(title):
        return hashlib.sha256(json.dumps({**content, 'translated_title': title}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    checkpoint = folder / 'title-review.json'
    if checkpoint.exists():
        old = json.loads(checkpoint.read_text('utf-8'))
        if old.get('signature') == signature(metadata['title']):
            return metadata
    result = chat(task['id'], settings,
        'Independently verify the Chinese title before publication. Check every person, chess player, tournament '
        'and year against the original title and description. Apply current_glossary (channel overrides global). '
        'Correct mistaken identities and transliterations. Never invent a person or event. If a Chinese name is '
        'uncertain, retain its original Latin name rather than guessing. Preserve the title prefix and meaning. '
        'Return {"title":"corrected title, max 80 characters","verified":true,"entities":'
        '[{"original":"source name","translation":"title name"}]}. verified may be true only when all '
        'names are accounted for. No explanations in the title.', content)
    title = result.get('title')
    entities = result.get('entities')
    if (result.get('verified') is not True or not isinstance(title, str) or not 0 < len(title.strip()) <= 80
            or not isinstance(entities, list) or any(not isinstance(e, dict)
            or not isinstance(e.get('original'), str) or not e['original'].strip()
            or e['original'].casefold() not in source['title'].casefold()
            or not isinstance(e.get('translation'), str) or not e['translation'].strip()
            or e['translation'] not in title for e in entities)):
        raise Waiting('发布前标题名称校验未通过，请检查原文与术语表后重试')
    metadata['title'] = title.strip()
    save(folder / 'posting.json', metadata)
    save(checkpoint, {**result, 'signature': signature(metadata['title'])})
    store.update(task['id'], title=metadata['title'])
    store.event(task['id'], '发布前标题名称校验通过' + ('，已修正标题' if title.strip() != content['translated_title'] else ''))
    return metadata
