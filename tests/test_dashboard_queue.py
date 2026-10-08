import json,time
from app import store,worker,config
from app.media import RetryLater

def enqueue():
 return store.enqueue('abcdefghijk','https://www.youtube.com/watch?v=abcdefghijk')

def test_task_pagination_and_visible_stats(client):
 with store.connect() as db:
  for i in range(325):
   db.execute("INSERT INTO tasks(id,video_id,url,title,created,updated,status) VALUES(?,?,?,?,?,?,?)",(str(i),f'{i:011d}','https://www.youtube.com/watch?v='+f'{i:011d}',f'video {i}',i,i,'completed'))
 store.execute('UPDATE tasks SET deleted=1 WHERE id=?',('324',))
 overview=client.get('/api/overview').json()
 assert overview['stats']['total']==324 and overview['stats']['completed']==324
 assert len(overview['tasks'])==12
 first=client.get('/api/tasks').json()
 assert first['total']==324 and first['pages']==27 and len(first['tasks'])==12
 last=client.get('/api/tasks?page=999').json()
 assert last['page']==27 and last['tasks'][-1]['video_id']=='00000000000'
 assert client.get('/api/tasks?search=video%20323').json()['total']==1
 assert client.get('/api/tasks?search=%25').json()['total']==0
 assert client.get('/api/tasks?status=waiting').json()['total']==0
 assert client.get('/api/tasks?sort=oldest').json()['tasks'][0]['video_id']=='00000000000'
 assert client.get('/api/tasks?size=500').status_code==422

def test_resume_respects_cooldown(client):
 tid=enqueue(); until=time.time()+600
 store.set_runtime_state(worker.YOUTUBE_NETWORK_STATE,{'until':until})
 store.update(tid,status='retrying',attempts=4)
 assert client.post(f'/api/tasks/{tid}/action',json={'action':'resume'}).status_code==200
 t=store.task(tid)
 assert t['status']=='retrying' and t['next_run']==until and t['attempts']==4
 view=client.get('/api/tasks').json()['tasks'][0]
 assert view['retry_at']==until and view['retry_mode']=='automatic'

def test_ai_retry_is_bounded(client,monkeypatch):
 tid=enqueue();store.update(tid,status='running',stage='translate',attempts=11)
 folder=store.DATA/'media'/tid;folder.mkdir()
 (folder/'source.json').write_text('{}')
 def fail(*args):raise RetryLater('unavailable',30)
 monkeypatch.setattr(worker,'translate',fail)
 worker.process(tid)
 assert store.task(tid)['status']=='waiting'
 assert store.task(tid)['attempts']==12 and store.task(tid)['next_run']==0

def test_network_retry_is_bounded(client):
 tid=enqueue()
 store.set_runtime_state(worker.YOUTUBE_NETWORK_STATE,{'strikes':5,'last_at':time.time(),'until':0})
 worker._hold_youtube_network(tid,store.task(tid))
 assert store.task(tid)['status']=='waiting'
 assert '停止自动重试' in store.task(tid)['error']

def test_channel_check_respects_cooldown(client):
 c=client.post('/api/channels',json={'name':'Chess','url':'https://www.youtube.com/@agadmator/videos'}).json()['id']
 until=time.time()+600
 store.set_runtime_state(worker.YOUTUBE_RATE_STATE,{'until':until})
 assert client.post(f'/api/channels/{c}/check').status_code==409
 row=client.get('/api/channels').json()[0]
 assert row['next_check']==until and row['task_count']==0
 store.set_runtime_state(worker.YOUTUBE_RATE_STATE,{'until':0})
 assert client.post(f'/api/channels/{c}/check').status_code==200
