"""Local-only UI fixture. Never launches a browser or contacts BOSS."""
import hashlib
import json
import sys
import time

from app import boss_favorites as bf
from app.main import app
import uvicorn


def launch(task_id, prefs):
    results=[]
    for row in prefs['targets']:
        if prefs['action']=='match':
            closed='关闭' in row['title']
            exact=bool(row['company'])
            candidates=[]
            for i in range(1 if exact or row['source_url'] else 2):
                code=hashlib.sha256((row['title']+row['company']+str(i)).encode()).hexdigest()[:16]
                candidates.append({'url':row['source_url'] or 'https://www.zhipin.com/job_detail/'+code+'.html',
                  'title':row['title'] or '原链接岗位','company':row['company'] or ['候选甲企业','候选乙企业'][i],
                  'city':row['city'],'salary':'150–200 元/天','jd':'【界面测试资料】参与运营与用户调研。',
                  'checked_at':'2026-11-16T04:00:00Z','availability':'closed' if closed else 'active',
                  'favorite_status':'not_checked','selected':exact and not closed,
                  'match_reason':'公司、岗位和城市一致' if exact else '同名候选，请选择公司',
                  'error':'原岗位已关闭' if closed else ''})
            results.append({'id':row['id'],'state':'closed' if closed else 'matched' if exact else 'needs_choice','candidates':candidates})
        else:
            results.append({**row,'id':row['id'],'availability':'active','favorite_status':'already_saved' if row.get('favorite_status')=='saved' else 'saved',
              'selected':False,'checked_at':'2026-11-16T04:05:00Z','error':''})
    output={'task_id':task_id,'run_id':task_id,'status':'completed','items':[],'results':results,'total':0,
      'heartbeat':time.time(),'error':'','finished_at':'2026-11-16T04:05:00Z',
      'platform_state':{'boss':{'phase':'已完成','message':'界面测试：已读取当前岗位' if prefs['action']=='match' else '界面测试：收藏状态已确认',
        'processed':len(results),'total':len(results),'cleanup_status':'complete','cleanup_message':'测试任务已结束'}}}
    directory=bf.run_directory(task_id);directory.mkdir(parents=True)
    (directory/'result.json').write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')


bf.launch=launch
bf.runner_command=lambda *args:['offline-ui-fixture']
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),access_log=False,log_level='warning')
