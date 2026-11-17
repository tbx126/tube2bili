import json
import pytest
from app import config,store,title_review
from app.bili_bridge import subtitle_data
from app.media import Waiting

def test_title_review_correction_cache_and_glossary(client,tmp_path,monkeypatch):
 tid=store.enqueue('abcdefghijk','https://www.youtube.com/watch?v=abcdefghijk')
 (tmp_path/'source.json').write_text(json.dumps({'title':'Javokhir Sindarov vs Carlsen'}))
 (tmp_path/'posting.json').write_text(json.dumps({'title':'贾沃赫尔对阵卡尔森','description':'desc'}))
 calls=[]
 def chat(*args):
  calls.append(args)
  return {'title':'辛达诺夫对阵卡尔森','verified':True,'entities':[{'original':'Javokhir Sindarov','translation':'辛达诺夫'}]}
 monkeypatch.setattr(title_review,'chat',chat)
 settings=config.get()
 assert title_review.review(store.task(tid),settings,tmp_path)['title']=='辛达诺夫对阵卡尔森'
 title_review.review(store.task(tid),settings,tmp_path)
 assert len(calls)==1
 settings.translation_notes+=' additional glossary'
 title_review.review(store.task(tid),settings,tmp_path)
 assert len(calls)==2
 assert store.task(tid)['title']=='辛达诺夫对阵卡尔森'

def test_title_review_rejects_wrong_identity(client,tmp_path,monkeypatch):
 tid=store.enqueue('abcdefghijk','https://www.youtube.com/watch?v=abcdefghijk')
 (tmp_path/'source.json').write_text(json.dumps({'title':'Carlsen'}))
 (tmp_path/'posting.json').write_text(json.dumps({'title':'卡尔森'}))
 monkeypatch.setattr(title_review,'chat',lambda *a:{'verified':True,'title':'辛达诺夫','entities':[{'original':'Sindarov','translation':'辛达诺夫'}]})
 with pytest.raises(Waiting):title_review.review(store.task(tid),config.get(),tmp_path)
 assert not (tmp_path/'title-review.json').exists()

def test_cc_limits_and_commentary(tmp_path):
 p=tmp_path/'en.srt'
 p.write_text('1\n00:00:01,000 --> 00:00:05,000\n'+'word '*50)
 body=subtitle_data(p)['body']
 assert all(len(x['content'].encode())<=80 for x in body)
 assert ''.join(x['content'] for x in body)==('word '*50).strip()
 assert body[0]['from']==1 and body[-1]['to']==5
 assert all(a['to']==b['from'] for a,b in zip(body,body[1:]))
 p.write_text('1\n00:00:01,000 --> 00:00:02,000\n（注：翻译解释）')
 with pytest.raises(ValueError):subtitle_data(p)


def test_title_review_accepts_name_evidence_in_description(client,tmp_path,monkeypatch):
 tid=store.enqueue('abcdefghijk','https://www.youtube.com/watch?v=abcdefghijk')
 (tmp_path/'source.json').write_text(json.dumps({'title':'An incredible game','description':'Gudmundur Gudmundsson vs Bobby Fischer, Iceland 1960'}))
 (tmp_path/'posting.json').write_text(json.dumps({'title':'古德蒙松对阵菲舍尔'}))
 monkeypatch.setattr(title_review,'chat',lambda *a:{'verified':True,'title':'古德蒙松对阵菲舍尔','entities':[{'original':'Gudmundur Gudmundsson','translation':'古德蒙松'},{'original':'Bobby Fischer','translation':'菲舍尔'}]})
 assert title_review.review(store.task(tid),config.get(),tmp_path)['title']=='古德蒙松对阵菲舍尔'
 assert (tmp_path/'title-review.json').exists()
