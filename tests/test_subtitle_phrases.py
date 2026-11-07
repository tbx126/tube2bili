import datetime as dt
import json

import pytest
import srt

from app import config, language, store
from app.bili_bridge import receipt_matches
from app.media import Waiting
from app.subtitle_phrases import group_words, prepare_intro, normalized_reference, trim_boundary_overlap, paired_cues


def word(start, text):
    return {'start': start, 'end': start + .3, 'text': text}


def test_phrases_keep_actual_word_times_and_split_at_connectors():
    words = [word(.8 + index * .4, token) for index, token in enumerate(
        ['Hello', 'everyone', 'and', 'welcome', 'to', 'another', 'great', 'game.'])]
    groups = group_words(words)
    assert [group['text'] for group in groups][:2] == ['Hello everyone', 'and welcome']
    assert groups[0]['start'] == .8 and groups[0]['end'] == words[1]['end']
    assert groups[1]['start'] == words[2]['start']
    assert ' '.join(group['text'] for group in groups) == ' '.join(w['text'] for w in words)
    assert all(group['start'] in [word['start'] for word in words] for group in groups)


def test_invalid_times_are_never_interpolated():
    with pytest.raises(Waiting):
        group_words([{'start': 2, 'end': 1, 'text': 'hello'}])
    with pytest.raises(Waiting):
        group_words([word(3, 'one'), word(1, 'two')])


def test_intro_reuses_words_preserves_tail_and_backs_up_checkpoints(client, tmp_path):
    task_id = store.enqueue('abcdefghijk', 'url')
    folder = tmp_path / 'task-media';folder.mkdir()
    settings = config.Settings(intro_phrase_seconds=3)
    cues = [srt.Subtitle(1, dt.timedelta(seconds=.8), dt.timedelta(seconds=2.5), 'Hello everyone and welcome'),
            srt.Subtitle(2, dt.timedelta(seconds=4), dt.timedelta(seconds=7), 'Unchanged later sentence')]
    (folder / 'en.srt').write_text(srt.compose(cues))
    (folder / 'translation-0.json').write_text('{"lines":[]}')
    words = [word(.8 + index*.4, token) for index, token in enumerate(['Hello', 'everyone', 'and', 'welcome'])]
    (folder / 'asr-0.json').write_text(json.dumps({'words': words, 'word_schema': 'lexical-v2'}))
    updated = prepare_intro(store.task(task_id), settings, folder, cues)
    assert len(updated) == 3
    assert [cue.index for cue in updated] == [1, 2, 3]
    assert (updated[-1].start, updated[-1].end, updated[-1].content) == (cues[-1].start, cues[-1].end, cues[-1].content)
    assert len(list(folder.glob('intro-backup-*'))) == 1
    assert not (folder / 'translation-0.json').exists()
    assert prepare_intro(store.task(task_id), settings, folder, updated) == updated
    assert len(list(folder.glob('intro-backup-*'))) == 1


def test_unrelated_recognition_keeps_original_subtitles(client, tmp_path):
    task_id = store.enqueue('abcdefghijk', 'url')
    cues = [srt.Subtitle(1, dt.timedelta(seconds=1), dt.timedelta(seconds=2), 'Hello everyone')]
    original = srt.compose(cues)
    (tmp_path / 'en.srt').write_text(original)
    (tmp_path / 'asr-0.json').write_text(json.dumps({'words': [word(1, 'unrelated')], 'word_schema': 'lexical-v2'}))
    with pytest.raises(Waiting, match='差异'):
        prepare_intro(store.task(task_id), config.Settings(intro_phrase_seconds=3), tmp_path, cues)
    assert (tmp_path / 'en.srt').read_text() == original
    assert not list(tmp_path.glob('intro-backup-*'))


def test_changed_subtitles_are_uploaded_again_only_when_revision_is_explicit(tmp_path):
    receipt = tmp_path / 'receipt.json'
    receipt.write_text('{"old_response":true}')
    assert receipt_matches(receipt, 'new')
    assert not receipt_matches(receipt, 'new', revise_legacy=True)
    receipt.write_text('{"content_sha256":"old"}')
    assert receipt_matches(receipt, 'old')
    assert not receipt_matches(receipt, 'new')


def test_year_readings_do_not_trigger_a_false_text_mismatch():
    assert normalized_reference('Played in two thousand and twenty four and twenty twenty six.') == normalized_reference('Played in 2 0 2 4 and 2026.')


def test_prefix_does_not_repeat_the_first_word_of_the_untouched_tail():
    words = [word(1, 'he'), word(1.4, 'finished'), word(1.8, 'fifth.')]
    assert trim_boundary_overlap(words, 'he finished', 'fifth. Next sentence', 2.1) == words[:-1]
    assert trim_boundary_overlap(words, 'he finished fifth', 'fifth. Next sentence', 2.1) == words


def test_repeated_chinese_translations_do_not_destroy_existing_pairs(tmp_path):
    en = [srt.Subtitle(1, dt.timedelta(seconds=1), dt.timedelta(seconds=2), 'Hello'),
          srt.Subtitle(2, dt.timedelta(seconds=2), dt.timedelta(seconds=3), 'Hi')]
    zh = [srt.Subtitle(c.index, c.start, c.end, '你好') for c in en]
    english, chinese = tmp_path/'en.srt', tmp_path/'zh.srt'
    english.write_text(srt.compose(en));chinese.write_text(srt.compose(zh),encoding='utf-8')
    actual_en, actual_zh = paired_cues(english, chinese)
    assert len(actual_en) == len(actual_zh) == 2
