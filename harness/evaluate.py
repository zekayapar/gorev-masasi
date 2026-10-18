"""External functional/security evaluator. Native audit guard is not OS isolation."""
import ast,json,tempfile,subprocess,time,socket,sys,urllib.request,urllib.error,urllib.parse,sqlite3
from pathlib import Path
R=Path(__file__).resolve().parent
PY='/home/zeka/ai/vllm/python/cpython-3.12.15-linux-x86_64-gnu/bin/python3.12'
NAMES=['health','html','login_invalid','login_cookie','me','unauth_tasks','unauth_stats','forged_cookie','create_defaults','create_full','read','update','delete','own_list','owner_read','owner_update','owner_delete','owner_stats','missing_id','unicode_search','literal_search','combined_filters','newest_first','filtered_total','invalid_title','invalid_description','invalid_enums','unknown_keys','wrong_types','malformed_json','root_json_type','body_limit','origin_login','origin_mutation','unsupported_method','no_file_disclosure','password_storage','session_logout','persistence','readme']
def parse(text,folder):
 t=text.strip();fenced=False
 if t.startswith('```') and t.splitlines()[-1].strip()=='```':t='\n'.join(t.splitlines()[1:-1]);fenced=True
 d=json.loads(t)
 if not isinstance(d,dict) or set(d)!={'app.py','index.html','README.md'} or not all(isinstance(v,str) for v in d.values()):raise ValueError('Expected JSON map with app.py,index.html,README.md strings')
 for n in ast.walk(ast.parse(d['app.py'])):
  if isinstance(n,(ast.Import,ast.ImportFrom)):
   mods=[a.name.split('.')[0] for a in n.names] if isinstance(n,ast.Import) else [str(n.module).split('.')[0]]
   if any(m not in sys.stdlib_module_names for m in mods):raise ValueError('Only standard library imports')
  if isinstance(n,ast.Name) and n.id in {'eval','exec','compile','__import__'}:raise ValueError('Dynamic execution forbidden')
  if isinstance(n,ast.Attribute) and n.attr in {'system','popen','environ','modules','fork','execv','execve','load_extension','enable_load_extension'}:raise ValueError('Unsafe attribute '+n.attr)
 folder.mkdir(parents=True,exist_ok=True)
 for k,v in d.items():(folder/k).write_text(v)
 return {'fence_removed':fenced}
class Server:
 def __init__(self,code,data):
  self.code=code;self.data=data;data.mkdir(parents=True,exist_ok=True)
  with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
 def start(self):
  self.log=(self.data/'server.log').open('a');self.p=subprocess.Popen([PY,str(R/'bootstrap.py'),str(self.code),str(self.port),str(self.data/'db.sqlite')],cwd=self.data,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'},stdout=self.log,stderr=self.log)
  deadline=time.monotonic()+8
  while time.monotonic()<deadline:
   if self.p.poll() is not None:break
   try:
    if self.req('GET','/health')[0]==200:return
   except Exception:pass
   time.sleep(.05)
  raise RuntimeError('startup failed: '+(self.data/'server.log').read_text()[-1600:])
 def stop(self):
  if hasattr(self,'p'):
   self.p.terminate()
   try:self.p.wait(timeout=3)
   except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
   self.log.close()
 def req(self,method,path,data=None,cookie=None,raw=None,origin=None):
  headers={'Content-Type':'application/json'}
  if cookie:headers['Cookie']=cookie
  if origin:headers['Origin']=origin
  body=raw if raw is not None else json.dumps(data,ensure_ascii=False).encode() if data is not None else None
  q=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=body,method=method,headers=headers)
  try:r=urllib.request.urlopen(q,timeout=4)
  except urllib.error.HTTPError as e:r=e
  with r:
   rawbody=r.read();ct=r.headers.get('content-type','');text=rawbody.decode('utf8');obj=json.loads(text) if 'json' in ct and text else text
   return r.status,obj,dict(r.headers),text
 def login(self,user='zeka'):
  st,o,h,_=self.req('POST','/api/login',{'username':user,'password':'deneme-'+user+'-2026'});assert st==200,(st,o)
  c=next((v for k,v in h.items() if k.lower()=='set-cookie'),'');assert c.startswith('session='),c;return c.split(';')[0]
def tests(s,code):
 rows=[]
 def check(name,fn):
  try:fn();rows.append({'name':name,'pass':True})
  except Exception as e:rows.append({'name':name,'pass':False,'error':str(e)[:900]})
 def eq(a,b):assert a==b,(a,b)
 def login(u='zeka'):return s.login(u)
 def req(m,p,d=None,c=None,**kw):return s.req(m,p,d,c or login(),**kw)
 def create(title='İlk görev',c=None,**kw):
  st,o,_,_=req('POST','/api/tasks',dict(title=title,**kw),c);eq(st,201);assert set(o)=={'id','title','description','status','priority'},o;assert type(o['id']) is int and o['id']>0;return o
 def error(r,st):
  eq(r[0],st);assert isinstance(r[1],dict) and isinstance(r[1].get('error'),str);assert all(v not in r[3] for v in ['Traceback','SELECT ','/home/zeka','deneme-zeka-2026'])
 check('health',lambda:eq(s.req('GET','/health')[:2],(200,{'ok':True})))
 check('html',lambda:(eq(s.req('GET','/')[0],200),eq('text/html' in str(s.req('GET','/')[2]).lower(),True)))
 def invalid():
  for u,p in [('zeka','wrong'),('missing','wrong'),("zeka' OR 1=1 --",'wrong')]:error(s.req('POST','/api/login',{'username':u,'password':p}),401)
 check('login_invalid',invalid)
 def cookie():
  st,o,h,_=s.req('POST','/api/login',{'username':'zeka','password':'deneme-zeka-2026'});eq(st,200);eq(o,{'username':'zeka'});v=next(v for k,v in h.items() if k.lower()=='set-cookie');assert all(x in v.lower() for x in ['httponly','samesite=strict','path=/']);assert len(v.split(';')[0].split('=',1)[1])>=24
 check('login_cookie',cookie)
 check('me',lambda:(eq(req('GET','/api/me')[:2],(200,{'username':'zeka'})),error(s.req('GET','/api/me'),401)))
 def unauth():
  for m,p,d in [('GET','/api/tasks',None),('POST','/api/tasks',{'title':'bad'}),('GET','/api/tasks/1',None),('PUT','/api/tasks/1',{'title':'bad'}),('DELETE','/api/tasks/1',None)]:error(s.req(m,p,d),401)
 check('unauth_tasks',unauth);check('unauth_stats',lambda:error(s.req('GET','/api/stats'),401));check('forged_cookie',lambda:error(s.req('GET','/api/tasks',cookie='session=zeka'),401))
 def defaults():
  o=create('  Kırpılmış  ');eq({k:v for k,v in o.items() if k!='id'},{'title':'Kırpılmış','description':'','status':'todo','priority':'normal'})
 check('create_defaults',defaults)
 def full():
  o=create('Tam görev',description='açıklama',status='doing',priority='high');eq(o['description'],'açıklama');eq(o['status'],'doing');eq(o['priority'],'high')
 check('create_full',full)
 def read():
  o=create();eq(req('GET',f'/api/tasks/{o["id"]}')[1],o)
 check('read',read)
 def update():
  o=create(status='done',priority='high',description='eski');st,n,_,_=req('PUT',f'/api/tasks/{o["id"]}',{'title':' yeni '});eq(st,200);eq(n,dict(id=o['id'],title='yeni',description='',status='todo',priority='normal'))
 check('update',update)
 def delete():
  o=create();eq(req('DELETE',f'/api/tasks/{o["id"]}')[:2],(200,{'deleted':True}));error(req('GET',f'/api/tasks/{o["id"]}'),404)
 check('delete',delete)
 def own():
  a=create('zeka private');b=create('yapar private',c=login('yapar'));z=req('GET','/api/tasks')[1]['items'];ids=[x['id'] for x in z];assert a['id'] in ids and b['id'] not in ids
 check('own_list',own)
 for m,name in [('GET','owner_read'),('PUT','owner_update'),('DELETE','owner_delete')]:
  def f(m=m):
   c=login();o=create('Korunan',c=c);error(req(m,f'/api/tasks/{o["id"]}',{'title':'Değişen'} if m=='PUT' else None,login('yapar')),404);eq(req('GET',f'/api/tasks/{o["id"]}',c=c)[1],o)
  check(name,f)
 def stats():
  c=login('yapar');before=req('GET','/api/stats',c=c)[1];create('Yapar done',c=c,status='done');create('Zeka done',status='done');after=req('GET','/api/stats',c=c)[1];eq(set(after),{'total','todo','doing','done'});eq(after['total'],before['total']+1);eq(after['done'],before['done']+1);eq(after['total'],after['todo']+after['doing']+after['done'])
 check('owner_stats',stats)
 def missing():
  for m in ['GET','PUT','DELETE']:error(req(m,'/api/tasks/999999999',{'title':'x'} if m=='PUT' else None),404)
 check('missing_id',missing)
 def unicode():
  o=create('Straße örneği',description='İstanbul');items=req('GET','/api/tasks?q=STRASSE')[1]['items'];assert o['id'] in [x['id'] for x in items]
 check('unicode_search',unicode)
 def literal():
  o=create('yalnız %_ işareti');create('normal başlık');items=req('GET','/api/tasks?q=%25_')[1]['items'];assert o['id'] in [x['id'] for x in items] and all('%_' in x['title'] or '%_' in x['description'] for x in items)
 check('literal_search',literal)
 def filters():
  o=create('birleşik filtre',status='doing',priority='high');create('birleşik filtre',status='todo',priority='high');items=req('GET','/api/tasks?q='+urllib.parse.quote('birleşik')+'&status=doing&priority=high')[1]['items'];eq([x['id'] for x in items],[o['id']])
 check('combined_filters',filters)
 check('newest_first',lambda:(lambda ids:eq(ids,sorted(ids,reverse=True)))([x['id'] for x in req('GET','/api/tasks')[1]['items']]))
 def total():
  o=req('GET','/api/tasks?status=done')[1];eq(o['total'],len(o['items']));assert all(x['status']=='done' for x in o['items'])
 check('filtered_total',total)
 def invalid_tasks(changes):
  c=login();before=req('GET','/api/tasks',c=c)[1];existing=create('Atomic',c=c)
  for change in changes:
   d={'title':'valid'};d.update(change)
   if d.get('title') is None:d.pop('title')
   error(req('POST','/api/tasks',d,c),400);error(req('PUT',f'/api/tasks/{existing["id"]}',d,c),400);eq(req('GET',f'/api/tasks/{existing["id"]}',c=c)[1],existing)
  req('DELETE',f'/api/tasks/{existing["id"]}',c=c);eq(req('GET','/api/tasks',c=c)[1],before)
 check('invalid_title',lambda:invalid_tasks([{'title':None},{'title':' '},{'title':'a'*121}]))
 check('invalid_description',lambda:invalid_tasks([{'description':'a'*1001},{'description':False}]))
 def enums():
  invalid_tasks([{'status':'bad'},{'priority':'bad'}]);error(req('GET','/api/tasks?status=bad'),400);error(req('GET','/api/tasks?priority=bad'),400)
 check('invalid_enums',enums)
 check('unknown_keys',lambda:invalid_tasks([{'id':2},{'owner_id':2},{'unexpected':True}]))
 check('wrong_types',lambda:invalid_tasks([{'title':True},{'status':None},{'priority':42}]))
 check('malformed_json',lambda:error(req('POST','/api/tasks',raw=b'{broken'),400))
 check('root_json_type',lambda:error(req('POST','/api/tasks',raw=b'[]'),400))
 check('body_limit',lambda:error(req('POST','/api/tasks',raw=b'{"title":"'+b'a'*17000+b'"}'),413))
 check('origin_login',lambda:error(s.req('POST','/api/login',{'username':'zeka','password':'deneme-zeka-2026'},origin='https://untrusted.invalid'),403))
 def origin():
  c=login();before=req('GET','/api/tasks',c=c)[1];error(req('POST','/api/tasks',{'title':'evil'},c,origin='https://untrusted.invalid'),403);eq(req('GET','/api/tasks',c=c)[1],before)
  eq(req('POST','/api/tasks',{'title':'same origin'},c,origin=f'http://127.0.0.1:{s.port}')[0],201)
 check('origin_mutation',origin)
 check('unsupported_method',lambda:error(req('PATCH','/api/tasks',{'title':'x'}),405))
 def disclosure():
  for p in ['/app.py','/README.md','/db.sqlite','/../../etc/passwd']:
   r=s.req('GET',p);assert r[0] in (403,404);assert 'deneme-zeka-2026' not in r[3]
 check('no_file_disclosure',disclosure)
 def password():
  # Check actual persisted rows, not just source text. Cryptographic parameters are inspected separately.
  db=sqlite3.connect(s.data/'db.sqlite')
  try:
   tabs=[x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")];assert tabs
   rows=[tuple(row) for table in tabs for row in db.execute('SELECT * FROM "'+table.replace('"','""')+'"')]
   assert rows;assert all('deneme-zeka-2026' not in str(row) and 'deneme-yapar-2026' not in str(row) for row in rows)
   src=(code/'app.py').read_text();assert 'pbkdf2_hmac' in src or 'scrypt' in src,'No requested password KDF in source'
  finally:db.close()
 check('password_storage',password)
 def logout():
  c=login();eq(req('POST','/api/logout',{},c)[0],200);error(s.req('GET','/api/me',cookie=c),401)
 check('session_logout',logout)
 def persistence():
  o=create('restart kalıcılık');s.stop();s.start();eq(req('GET',f'/api/tasks/{o["id"]}')[1],o)
 check('persistence',persistence)
 def readme():
  text=(code/'README.md').read_text().lower();assert all(x in text for x in ['--port','--db','zeka','yapar','sqlite']);assert len(text)>150
 check('readme',readme)
 assert [x['name'] for x in rows]==NAMES
 return rows

def evaluate(text,folder):
 out={'format_pass':False,'startup_pass':False,'checks':[],'passed':0,'total':len(NAMES),'all_pass':False};s=None;start=time.monotonic()
 try:
  out.update(parse(text,folder));out['format_pass']=True
  data=folder.parent/'data';s=Server(folder,data);s.start();out['startup_pass']=True;out['checks']=tests(s,folder);out['passed']=sum(x['pass'] for x in out['checks']);out['all_pass']=out['passed']==len(NAMES)
 except Exception as e:out['error']=str(e)[:2000]
 finally:
  if s:s.stop()
 out['evaluation_s']=time.monotonic()-start;return out
if __name__=='__main__':print(json.dumps(evaluate(Path(sys.argv[1]).read_text(),Path(sys.argv[2])),ensure_ascii=False,indent=2))
